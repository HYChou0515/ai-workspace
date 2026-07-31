"""What a third-party tool author's CI runs (#674).

The author never learns our internals — they run one command inside the
builder image and publish what falls out:

    build-tool /src dist   ->  dist/tool.tar.gz  +  dist/tool.manifest.json

The image matters as much as the command. A bundle carries its own portable
python and native wheels, so it only runs on the base it was built against;
building inside the sandbox's own runtime base is what makes ABI compatibility
structural instead of a gamble. `builder_id` records which base that was, and
the platform refuses anything built against another.

Compression is stdlib gzip, not zstd. zstd would be smaller and faster, but
the party that has to DECOMPRESS is `sandbox-host` — a deliberately minimal,
root-running service whose pyproject says in as many words that it shares no
dependencies with the app. Handing it a C extension so it can unpack a
stranger's bytes buys a few seconds once per (host, sha) and costs attack
surface in the worst possible place.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path

from workspace_app.tooling.artifact import (
    ArtifactError,
    BundleRef,
    CommandSpec,
    Manifest,
    parse_manifest,
    render_manifest,
    verify_bundle,
)

#: The two file names an author's CI publishes. The platform is given the
#: manifest's URL and derives the bundle's by swapping the basename, so these
#: are contract, not convention.
MANIFEST_NAME = "tool.manifest.json"
BUNDLE_NAME = "tool.tar.gz"


class BuildError(ArtifactError):
    """The source tree cannot be turned into a publishable artifact."""


class SmokeFailed(ArtifactError):
    """The built bundle does not actually run, or does not do what its
    manifest says. Publishing it would move the failure onto a user."""


def _default_build_bundle(*, name: str, source: Path, dst: Path) -> None:
    """Import lazily: `prebuild` reaches for uv and a network, which the pure
    parts of a build (and their tests) have no business requiring."""
    from workspace_app.tooling import prebuild

    prebuild.build_package(name=name, source=source, dst=dst, force=True)


def read_project(source: Path) -> tuple[str, str]:
    """The tool's name and version, from the author's own ``pyproject.toml``.

    The name is the ``[project.scripts]`` key rather than ``[project].name``
    because that is the entry point the bundle's launcher invokes — the same
    rule first-party packages already live under."""
    path = source / "pyproject.toml"
    try:
        body = tomllib.loads(path.read_text("utf-8"))
    except FileNotFoundError as exc:
        raise BuildError(f"no pyproject.toml at {source}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BuildError(f"pyproject.toml is not valid toml: {exc}") from exc

    scripts = body.get("project", {}).get("scripts", {})
    if len(scripts) != 1:
        raise BuildError(
            f"expected exactly one [project.scripts] entry, found {sorted(scripts)} — "
            "the single console script is the command the bundle's launcher runs"
        )
    version = body.get("project", {}).get("version")
    if not version:
        raise BuildError("[project].version is required — it is how a human names a release")
    return next(iter(scripts)), str(version)


def read_commands(bundle: Path) -> tuple[CommandSpec, ...]:
    """The command metadata the build froze into the bundle.

    Read back out of the bundle rather than re-derived, so the manifest can
    never describe commands the shipped tree does not actually have."""
    try:
        listed = json.loads((bundle / "commands.json").read_text("utf-8"))
    except FileNotFoundError as exc:
        raise BuildError("bundle has no commands.json — the build did not complete") from exc

    specs: list[CommandSpec] = []
    for entry in listed:
        name = entry["name"]
        schema = json.loads((bundle / "schemas" / f"{name}.json").read_text("utf-8"))
        specs.append(
            CommandSpec(
                name=name,
                description=schema["description"],
                params_json_schema=schema["params_json_schema"],
            )
        )
    return tuple(specs)


def pack_bundle(bundle: Path) -> bytes:
    """Tar+gzip the bundle's CONTENTS, not a directory named after it.

    The host mounts the extracted tree at `/.tools/<local name>`, and the local
    name is ours (app.json's key) — a top-level directory carrying the author's
    name would fight that."""
    buf = io.BytesIO()
    # mtime=0: two builds of identical content should produce identical bytes,
    # so an unchanged tool keeps its sha and the host's cache stays warm.
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for path in sorted(bundle.rglob("*")):
            info = tar.gettarinfo(path, arcname=str(path.relative_to(bundle)))
            if info.issym() and info.linkname.startswith("/"):
                info.linkname = _relocatable_link(bundle, path, info.linkname)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path.is_file():
                with path.open("rb") as fh:
                    tar.addfile(info, fh)
            else:
                tar.addfile(info)
    return buf.getvalue()


def _relocatable_link(bundle: Path, link: Path, target: str) -> str:
    """Turn an absolute symlink into one that survives being moved.

    `uv venv` leaves `.venv/bin/python` pointing at the ABSOLUTE path of the
    interpreter it built against — a path on the build machine, meaningless
    anywhere the bundle is going. It has always been dangling once relocated;
    it only went unnoticed because the launcher invokes the bundle's own
    interpreter directly and never follows it. Packing is where a bundle stops
    being a directory on one machine and becomes bytes for another, so it is
    where the link has to be made honest.

    A target that already lives inside the bundle is simply rewritten as
    relative. An interpreter outside it is repointed at the one the bundle
    ships. Anything else is refused: a bundle that links out of itself is not
    self-contained, and the safe tar filter on the other side would reject it
    anyway — better to fail the author's build than to publish something no
    host will unpack."""
    inside = Path(target)
    if inside.is_relative_to(bundle):
        return os.path.relpath(inside, link.parent)

    shipped = bundle / "python" / "bin" / Path(target).name
    if shipped.exists():
        return os.path.relpath(shipped, link.parent)

    raise BuildError(
        f"{link.relative_to(bundle)} links to {target}, which is outside the bundle — "
        "a bundle must carry everything it needs, and nothing that unpacks it will "
        "follow a link off its own tree"
    )


def build_artifact(
    *,
    source: Path,
    out: Path,
    builder_id: str,
    arch: str | None = None,
    python: str | None = None,
    build_bundle: Callable[..., None] = _default_build_bundle,
    smoke_check: Callable[[Path], None] | None = None,
) -> Manifest:
    """Build an author's source tree into the artifact pair, and return the
    manifest that was published beside the bundle."""
    name, version = read_project(source)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / name
        build_bundle(name=name, source=source, dst=bundle)
        commands = read_commands(bundle)
        packed = pack_bundle(bundle)

    manifest = Manifest(
        format_version=1,
        name=name,
        version=version,
        commands=commands,
        builder=builder_id,
        python=python or f"{sys.version_info.major}.{sys.version_info.minor}",
        arch=arch or platform.machine(),
        bundle=BundleRef(sha256=hashlib.sha256(packed).hexdigest(), size=len(packed)),
        source=None,
    )
    (out / BUNDLE_NAME).write_bytes(packed)
    (out / MANIFEST_NAME).write_bytes(render_manifest(manifest))

    try:
        (smoke_check or smoke)(out)
    except ArtifactError:
        # Leave NOTHING behind. A dist directory holding a bundle that
        # failed its own smoke is worse than no dist at all: CI would
        # happily publish it, and the failure would surface as a user's
        # tool call breaking rather than as a red build.
        shutil.rmtree(out, ignore_errors=True)
        raise
    return manifest


def _extract(data: bytes, dst: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        # `data` filter: refuse absolute paths, `..` escapes and special files.
        # This is our own tarball, but the same bytes reach the host from a
        # stranger, so the safe habit belongs on both sides of the wire.
        tar.extractall(dst, filter="data")


def _launch(launcher: Path, *args: str) -> str:
    proc = subprocess.run(  # noqa: S603
        [str(launcher), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        shown = " ".join(args) or "(no arguments)"
        raise SmokeFailed(f"`launch {shown}` exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return proc.stdout


def _launch_json[T](launcher: Path, *args: str, expect: type[T]) -> T:
    """Parse a launcher's stdout, insisting on the shape the contract promises.

    A launcher that prints a traceback, or a bare string where the contract
    says a list, has still "succeeded" as far as its exit code goes — so the
    shape check is the only thing standing between that and a published tool
    the agent cannot call."""
    shown = " ".join(args) or "(no arguments)"
    out = _launch(launcher, *args)
    try:
        value = json.loads(out)
    except ValueError as exc:
        raise SmokeFailed(
            f"`launch {shown}` printed something that is not json ({exc}): {out.strip()[:500]}"
        ) from exc
    if not isinstance(value, expect):
        raise SmokeFailed(
            f"`launch {shown}` printed {type(value).__name__}, the 3-stage "
            f"contract says {expect.__name__}"
        )
    return value


def smoke(dist: Path) -> None:
    """Run the published bundle and check it against its own manifest.

    This is the only requirement the platform can actually enforce on an
    author, so it belongs INSIDE the build (see `build_artifact`) rather than
    in a CI job that can be skipped or ignored."""
    manifest = parse_manifest((dist / MANIFEST_NAME).read_bytes())
    data = (dist / BUNDLE_NAME).read_bytes()
    verify_bundle(data, manifest.bundle)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _extract(data, root)
        launcher = root / "launch"

        listed = _launch_json(launcher, expect=list)
        declared = [c.name for c in manifest.commands]
        if [entry["name"] for entry in listed] != declared:
            raise SmokeFailed(
                f"the bundle lists {[e['name'] for e in listed]} but its manifest "
                f"declares {declared} — the schemas were frozen from a different build"
            )

        for command in manifest.commands:
            spec = _launch_json(launcher, command.name, expect=dict)
            if spec["params_json_schema"] != command.params_json_schema:
                raise SmokeFailed(
                    f"`{command.name}` accepts a different schema than the manifest "
                    "publishes — the agent would be told the wrong arguments"
                )


_USAGE = "usage: build-tool <source-dir> <out-dir> | smoke <dist-dir>"


def main(argv: list[str]) -> int:
    """The two commands the builder image exposes.

    Kept deliberately thin: an author reads its output in a CI log, so the
    only jobs here are to name what was published and to turn a refusal into
    a red build rather than a quiet one."""
    if len(argv) == 3 and argv[0] == "build":
        builder_id = os.environ.get("TOOL_BUILDER_ID")
        if not builder_id:
            print(
                "TOOL_BUILDER_ID is not set — build-tool must run inside the "
                "builder image, whose identity is the ABI anchor the platform "
                "gates on",
                file=sys.stderr,
            )
            return 2
        try:
            manifest = build_artifact(
                source=Path(argv[1]), out=Path(argv[2]), builder_id=builder_id
            )
        except ArtifactError as exc:
            print(f"build failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"published {manifest.name} {manifest.version} "
            f"({len(manifest.commands)} command(s), sha256={manifest.bundle.sha256})"
        )
        return 0

    if len(argv) == 2 and argv[0] == "smoke":
        try:
            smoke(Path(argv[1]))
        except ArtifactError as exc:
            print(f"smoke failed: {exc}", file=sys.stderr)
            return 1
        print("smoke passed")
        return 0

    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    raise SystemExit(main(sys.argv[1:]))

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workspace_app.tooling import grant as grant_policy
from workspace_app.tooling.artifact import (
    BUNDLE_NAME,
    MANIFEST_NAME,
    ArtifactError,
    BundleRef,
    CommandSpec,
    EnvSpec,
    Manifest,
    parse_env_declaration,
    parse_manifest,
    render_manifest,
    verify_bundle,
)

# The two names an author's CI publishes live in the contract both packages
# carry, beside the rule that derives one URL from the other. Re-exported here
# because this is where they are written.


class BuildError(ArtifactError):
    """The source tree cannot be turned into a publishable artifact."""


def _today():  # pragma: no cover - trivial, seamed so expiry is testable
    from datetime import date

    return date.today()


def _du(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _heaviest(bundle: Path, count: int = 6) -> list[tuple[str, int]]:
    """The entries that account for a bundle's weight, heaviest first.

    "Too big" on its own sends an author to guess at their dependency tree.
    The build has that tree in front of it, so it can name what to look at."""
    weighed: list[tuple[str, int]] = []
    interpreter = bundle / "python"
    if interpreter.is_dir():
        # Named, though it cannot be removed: seeing that half the bundle is
        # the interpreter is what stops someone hunting for a package to cut
        # that would not have helped.
        weighed.append(("the bundled interpreter", _du(interpreter)))
    for site in (bundle / ".venv" / "lib").glob("python*/site-packages"):
        weighed += [
            (entry.name, _du(entry))
            for entry in site.iterdir()
            if entry.is_dir() and not entry.name.endswith((".dist-info", "__pycache__"))
        ]
    # Only what accounts for the weight: an entry worth a fraction of a
    # percent is noise, and a list of everything installed helps as little as
    # no list at all. One percent of the total, heaviest first.
    total = sum(size for _, size in weighed) or 1
    ranked = sorted(weighed, key=lambda item: -item[1])
    return [item for item in ranked if item[1] * 100 >= total][:count]


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MB"


def _read_grant(source: Path) -> str | None:
    token = source / grant_policy.GRANT_FILE
    return token.read_text().strip() if token.is_file() else None


def _check_weight(*, name: str, bundle: Path, packed: int, token: str | None) -> None:
    """Refuse a bundle heavier than this tool is allowed to be.

    Measured on the compressed artifact, which is what every host downloads
    and what the manifest records. The rule itself lives in `grant.check_size`
    so this and the platform's gate cannot drift apart; what belongs here is
    the part only a build can supply — which entries account for the weight.

    No name is passed, and none could be: the certificate carries the
    platform's name for this tool, and a build only knows the one in
    `[project.scripts]`."""
    reason = grant_policy.check_size(
        size=packed,
        token=token,
        public_keys=grant_policy.TRUSTED_KEYS,
        today=_today(),
    )
    if reason is None:
        return
    largest = ", ".join(f"{what} {_mb(size)}" for what, size in _heaviest(bundle))
    raise BuildError(
        f"{reason}. Its weight is mostly: {largest}. "
        "Keep what the tool needs at run time — dependencies used only by your "
        "tests belong in `[dependency-groups] dev`, which the build already "
        "leaves out. When the weight is real, send the tool to the platform "
        "team for review and they can issue a certificate raising this limit."
    )


class SmokeFailed(ArtifactError):
    """The built bundle does not actually run, or does not do what its
    manifest says. Publishing it would move the failure onto a user."""


def _default_build_bundle(*, name: str, source: Path, dst: Path) -> None:
    """Import lazily: `prebuild` reaches for uv and a network, which the pure
    parts of a build (and their tests) have no business requiring."""
    from workspace_app.tooling import prebuild

    prebuild.build_package(name=name, source=source, dst=dst, force=True)


@dataclass(frozen=True)
class Project:
    """What an author's own ``pyproject.toml`` settles about their tool."""

    name: str
    version: str
    author: str | None
    """``None`` when they declared no usable ``[project].authors`` — see
    ``_read_author``."""
    description: str | None = None
    """What the tool IS, from ``[project].description``. ``None`` when they
    wrote none — encouraged, never required, exactly like the author."""


def _read_author(project: dict[str, Any]) -> str | None:
    """Render ``[project].authors`` as one display string, or ``None``.

    PEP 621 already gives every author a place to say who they are, so #724
    adds no new field for them to fill in: the file they must edit to cut a
    release is the file we read.

    Lenient on purpose. This string decides nothing downstream — it is shown,
    never gated on — so a table we cannot make sense of contributes nothing
    rather than failing a build over a courtesy. `main` says what it published,
    which is where an author sees that theirs did not come through."""
    listed = project.get("authors")
    if not isinstance(listed, list):
        return None
    rendered = []
    for entry in listed:
        if not isinstance(entry, dict):
            continue
        name, email = entry.get("name"), entry.get("email")
        if name and email:
            rendered.append(f"{name} <{email}>")
        elif name or email:
            # A bare email reads better than `<email>` in a sentence that
            # already says "by".
            rendered.append(str(name or email))
    return ", ".join(rendered) or None


def read_env_declaration(source: Path) -> tuple[EnvSpec, ...] | None:
    """What the author says their tool needs from the environment (#750).

    Read from the SOURCE, beside `author` and `description`: this is
    provenance the manifest carries, not something derived from the built
    tree. It has to reach the MANIFEST — the host reads `manifest.env` and
    never reads `env.json` out of the bundle it unpacks, so a declaration
    that only lands in the tarball is one the platform never sees (#763).

    Absent stays absent. `None` means the author never wrote the file, which
    is a different claim from an empty list, and both travel all the way to
    the panel.

    Everything that is NOT absence is named instead: a directory, an
    unreadable file, a symlink to nothing. Reading those as "the author said
    nothing" would be #763 itself coming back through a different door — a
    green build that publishes silence.
    """
    declared = source / "env.json"
    if not declared.exists():
        if declared.is_symlink():
            raise BuildError(f"{declared} is a symlink to nothing — refusing to publish silence")
        return None
    try:
        # utf-8-sig, not utf-8: this is a file the docs tell a third party to
        # hand-write, and a Windows editor puts a BOM on it. It reads plain
        # UTF-8 identically, so accepting one costs nothing.
        raw = declared.read_text("utf-8-sig")
    except OSError as exc:
        raise BuildError(f"{declared} could not be read: {exc}") from exc
    try:
        return parse_env_declaration(raw)
    except ValueError as exc:
        raise BuildError(f"{declared} is not a usable environment declaration: {exc}") from exc


def read_project(source: Path) -> Project:
    """The tool's name, version and author, from their own ``pyproject.toml``.

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

    project = body.get("project", {})
    scripts = project.get("scripts", {})
    if len(scripts) != 1:
        raise BuildError(
            f"expected exactly one [project.scripts] entry, found {sorted(scripts)} — "
            "the single console script is the command the bundle's launcher runs"
        )
    version = project.get("version")
    if not version:
        raise BuildError("[project].version is required — it is how a human names a release")
    # PEP 621 already gives an author somewhere to say what their tool is, so
    # #697 adds no field for them to fill: same rule as the author above.
    described = project.get("description")
    return Project(
        name=next(iter(scripts)),
        version=str(version),
        author=_read_author(project),
        description=str(described) if described else None,
    )


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
            if info.issym():
                # Absolute links are rewritten if they can be; relative ones
                # are checked, because a relative link can climb out just as
                # well and used to pack cleanly — then be refused by the
                # host's `data` filter, which is the failure this step exists
                # to move from a stranger's machine to the author's build.
                #
                # Compared lexically, as the filter does. Resolving would
                # follow the whole chain, and a real venv's `python3 ->
                # python3.12 -> /usr/...` ends up outside while every link in
                # it stays inside.
                if info.linkname.startswith("/"):
                    info.linkname = _relocatable_link(bundle, path, info.linkname)
                elif not Path(os.path.normpath(path.parent / info.linkname)).is_relative_to(
                    os.path.normpath(bundle)
                ):
                    raise BuildError(
                        f"{path.relative_to(bundle)} points outside the bundle "
                        f"({info.linkname}). No host will unpack that, so it fails here."
                    )
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


#: The MCP entry point injected into every bundle. It mirrors the tool
#: launcher's preamble — `readlink -f` so a symlinked shim still finds the
#: bundle, and the explicit dynamic loader — then runs the adapter on the
#: interpreter the bundle ships. Falling back to a bare exec when no loader is
#: found keeps it working outside the jail the loader trick exists for.
_MCP_LAUNCH = """\
#!/bin/sh
self=$(readlink -f "$0" 2>/dev/null || echo "$0")
here=$(CDPATH= cd -- "$(dirname -- "$self")" && pwd)
py="$here/python/bin/python{ver}"
ld=$(ls /lib64/ld-linux-x86-64.so.2 /lib/ld-linux-aarch64.so.1 2>/dev/null | head -n1)
if [ -n "$ld" ]; then
  exec "$ld" "$py" "$here/mcp_server.py" "$here"
fi
exec "$py" "$here/mcp_server.py" "$here"
"""


def _inject_mcp(bundle: Path) -> None:
    """Give the bundle its MCP face.

    Injected rather than written by the author: the 3-stage contract already
    carries everything MCP asks for, so one adapter serves every tool and
    nobody has to learn a second protocol to publish one."""
    shutil.copy2(Path(__file__).with_name("mcp_server.py"), bundle / "mcp_server.py")

    # The bundle ships exactly one minor version; take it from the interpreter
    # that is actually there rather than from ours, which may differ.
    interpreters = sorted((bundle / "python" / "bin").glob("python3.*"))
    if not interpreters:
        raise BuildError("the bundle carries no interpreter — the build did not complete")
    version = interpreters[0].name.removeprefix("python")

    entry = bundle / "mcp"
    entry.write_text(_MCP_LAUNCH.format(ver=version))
    entry.chmod(0o755)


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
    project = read_project(source)
    name, version = project.name, project.version
    token = _read_grant(source)
    # Read with the other source-derived provenance, BEFORE anything is
    # written: a refusal must leave no dist behind (the same reason the weight
    # check runs inside the temp dir below), and reading it here is also what
    # puts the named `BuildError` in front of the author. Deeper in, the
    # bundle build has already refused the same file as a bare `RuntimeError`
    # that `main` cannot catch, so the author's CI log gets a traceback
    # instead of one `build failed:` line.
    env_needs = read_env_declaration(source)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / name
        build_bundle(name=name, source=source, dst=bundle)
        _inject_mcp(bundle)
        commands = read_commands(bundle)
        packed = pack_bundle(bundle)
        # Inside the temp dir, and before anything is written to `out`: the
        # diagnostic needs the tree, and a refusal must leave no dist behind
        # for CI to publish.
        _check_weight(name=name, bundle=bundle, packed=len(packed), token=token)

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
        grant=token,
        author=project.author,
        description=project.description,
        env=env_needs,
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
        by = f" by {manifest.author}" if manifest.author else ""
        print(
            f"published {manifest.name} {manifest.version}{by} "
            f"({len(manifest.commands)} command(s), sha256={manifest.bundle.sha256})"
        )
        if not manifest.author:
            # The only feedback loop this field has. Nothing refuses a build
            # over it, so an author who meant to be reachable and mistyped the
            # table would otherwise never find out.
            print(
                "note: no author published — add `authors = [{name = ..., email = ...}]` "
                "to [project] so whoever hits a problem knows who to ask"
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

"""P2 — what a tool author's CI runs (#674).

`build_artifact` turns an author's source tree into the two files their CI
publishes; `smoke` proves the result actually runs before it is allowed out.

The expensive half (uv venv + native wheels + a copied interpreter) is
`prebuild.build_package`, injected here so these stay unit tests. A real
build is exercised by the integration test in `test_end_to_end.py`.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from workspace_app.tooling import builder as builder_mod
from workspace_app.tooling.artifact import BundleRef, Manifest, parse_manifest
from workspace_app.tooling.builder import (
    BUNDLE_NAME,
    MANIFEST_NAME,
    BuildError,
    SmokeFailed,
    _default_build_bundle,
    build_artifact,
    main,
    read_commands,
    read_project,
    smoke,
)

_BUILDER = "registry.example/tool-builder@sha256:beef"


def _source(tmp_path: Path, *, name: str = "wafer-history", version: str = "1.4.2") -> Path:
    src = tmp_path / "src-tree"
    (src / "src").mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n'
        f'\n[project.scripts]\n{name} = "pkg.cli:main"\n'
    )
    (src / "uv.lock").write_text("# pinned")
    return src


def _fake_bundle(commands: dict[str, str]):
    """Stand in for `prebuild.build_package`: lay down the bundle shape it
    produces, without the venv it takes minutes to build."""

    def build(*, name: str, source: Path, dst: Path) -> None:  # noqa: ARG001
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "launch").write_text("#!/bin/sh\n")
        (dst / "launch").chmod(0o755)
        (dst / "commands.json").write_text(
            json.dumps([{"name": c, "description": d} for c, d in commands.items()])
        )
        schemas = dst / "schemas"
        schemas.mkdir()
        for c, d in commands.items():
            (schemas / f"{c}.json").write_text(
                json.dumps(
                    {
                        "name": c,
                        "description": d,
                        "params_json_schema": {"type": "object", "properties": {}},
                    }
                )
            )

    return build


def test_build_artifact_emits_the_two_files_the_author_publishes(tmp_path: Path) -> None:
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "Yield trend for a lot."}),
        smoke_check=lambda _dist: None,
    )

    bundle = (out / BUNDLE_NAME).read_bytes()
    published = parse_manifest((out / MANIFEST_NAME).read_bytes())

    assert published == manifest
    assert published.name == "wafer-history"
    assert published.version == "1.4.2"
    assert published.builder == _BUILDER
    # The sha the platform will gate on has to describe the bytes shipped
    # beside it, or nothing downstream can be trusted.
    assert published.bundle.sha256 == hashlib.sha256(bundle).hexdigest()
    assert published.bundle.size == len(bundle)
    assert [c.name for c in published.commands] == ["trend"]
    assert published.commands[0].description == "Yield trend for a lot."


def test_the_published_bundle_carries_the_runnable_tree(tmp_path: Path) -> None:
    out = tmp_path / "dist"

    build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    with tarfile.open(fileobj=io.BytesIO((out / BUNDLE_NAME).read_bytes())) as tar:
        names = set(tar.getnames())

    # Rooted at the bundle's own contents, not at a directory named after the
    # tool: the host mounts it as `/.tools/<local name>`, which is OUR name.
    assert "launch" in names
    assert "commands.json" in names
    assert "schemas/trend.json" in names


def _launcher(body: str) -> str:
    return f"#!/bin/sh\n{body}\n"


_HONEST = _launcher(
    'if [ $# -eq 0 ]; then echo \'[{"name":"trend","description":"t"}]\'; exit 0; fi\n'
    'if [ "$1" = "trend" ]; then'
    ' echo \'{"name":"trend","description":"t",'
    '"params_json_schema":{"type":"object","properties":{}}}\';'
    " exit 0; fi\n"
    "exit 1"
)


def _dist_with_launcher(tmp_path: Path, launcher: str) -> Path:
    """A published dist whose bundle carries a real, runnable launcher."""
    out = tmp_path / "dist"

    def build(*, name: str, source: Path, dst: Path) -> None:  # noqa: ARG001
        _fake_bundle({"trend": "t"})(name=name, source=source, dst=dst)
        (dst / "launch").write_text(launcher)
        (dst / "launch").chmod(0o755)

    build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=build,
        smoke_check=lambda _dist: None,
    )
    return out


def test_smoke_accepts_a_bundle_whose_launcher_matches_its_manifest(tmp_path: Path) -> None:
    smoke(_dist_with_launcher(tmp_path, _HONEST))  # does not raise


def test_smoke_rejects_a_bundle_that_does_not_run(tmp_path: Path) -> None:
    dist = _dist_with_launcher(tmp_path, _launcher("exit 3"))

    with pytest.raises(SmokeFailed) as exc:
        smoke(dist)

    assert "3" in str(exc.value)


def test_smoke_reports_a_launcher_that_prints_something_that_is_not_json(
    tmp_path: Path,
) -> None:
    dist = _dist_with_launcher(tmp_path, _launcher("echo 'Traceback (most recent call last)'"))

    with pytest.raises(SmokeFailed) as exc:
        smoke(dist)

    assert "Traceback" in str(exc.value)


def test_a_bundle_that_fails_smoke_leaves_no_artifact_to_publish(tmp_path: Path) -> None:
    # Q15: this is the only requirement we can actually enforce on an author,
    # so a red build must not leave a dist/ that CI would happily upload.
    out = tmp_path / "dist"

    def build(*, name: str, source: Path, dst: Path) -> None:  # noqa: ARG001
        _fake_bundle({"trend": "t"})(name=name, source=source, dst=dst)
        (dst / "launch").write_text(_launcher("exit 3"))
        (dst / "launch").chmod(0o755)

    with pytest.raises(SmokeFailed):
        build_artifact(source=_source(tmp_path), out=out, builder_id=_BUILDER, build_bundle=build)

    assert not out.exists()


def test_smoke_catches_a_bundle_that_lists_different_commands_than_it_publishes(
    tmp_path: Path,
) -> None:
    # The schemas were frozen from one build and the tree came from another:
    # the agent would be handed a tool that is not there.
    dist = _dist_with_launcher(tmp_path, _launcher('echo \'[{"name":"drift","description":"d"}]\''))

    with pytest.raises(SmokeFailed) as exc:
        smoke(dist)

    assert "drift" in str(exc.value)
    assert "trend" in str(exc.value)


def test_smoke_catches_a_command_whose_schema_drifted_from_the_manifest(
    tmp_path: Path,
) -> None:
    dist = _dist_with_launcher(
        tmp_path,
        _launcher(
            'if [ $# -eq 0 ]; then echo \'[{"name":"trend","description":"t"}]\'; exit 0; fi\n'
            'echo \'{"name":"trend","description":"t","params_json_schema":{"type":"object",'
            '"properties":{"lot":{"type":"string"}},"required":["lot"]}}\''
        ),
    )

    with pytest.raises(SmokeFailed) as exc:
        smoke(dist)

    assert "wrong arguments" in str(exc.value)


def test_smoke_rejects_a_launcher_that_prints_the_wrong_json_shape(tmp_path: Path) -> None:
    dist = _dist_with_launcher(tmp_path, _launcher("echo '\"just a string\"'"))

    with pytest.raises(SmokeFailed) as exc:
        smoke(dist)

    assert "str" in str(exc.value)
    assert "list" in str(exc.value)


def test_read_project_needs_a_pyproject(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="no pyproject.toml"):
        read_project(tmp_path)


def test_read_project_reports_unparseable_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\nname =")

    with pytest.raises(BuildError, match="not valid toml"):
        read_project(tmp_path)


def test_read_project_insists_on_exactly_one_console_script(tmp_path: Path) -> None:
    # The launcher invokes ONE entry point; two would make "which command did
    # the agent just run" ambiguous, and zero leaves nothing to run at all.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "t"\nversion = "1"\n\n[project.scripts]\na = "p:m"\nb = "p:m"\n'
    )

    with pytest.raises(BuildError, match="exactly one"):
        read_project(tmp_path)


def test_read_project_requires_a_version_humans_can_name(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "t"\n\n[project.scripts]\nt = "p:m"\n'
    )

    with pytest.raises(BuildError, match="version"):
        read_project(tmp_path)


def test_read_commands_reports_a_build_that_never_finished(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="did not complete"):
        read_commands(tmp_path)


def test_the_real_build_delegates_to_prebuild_and_forces_a_rebuild(monkeypatch) -> None:
    # `force=True`: a CI runner is a fresh checkout every time, so the
    # content-hash short circuit can only ever produce a stale surprise.
    from workspace_app.tooling import prebuild

    seen: dict[str, object] = {}
    monkeypatch.setattr(prebuild, "build_package", lambda **kw: seen.update(kw))

    _default_build_bundle(name="t", source=Path("/src"), dst=Path("/dst"))

    assert seen == {"name": "t", "source": Path("/src"), "dst": Path("/dst"), "force": True}


def _stub_manifest() -> Manifest:
    return Manifest(
        format_version=1,
        name="wafer-history",
        version="1.4.2",
        commands=(),
        builder=_BUILDER,
        python="3.12",
        arch="x86_64",
        bundle=BundleRef(sha256="a" * 64, size=1),
        source=None,
    )


def test_build_tool_wires_the_arguments_and_the_builder_identity(tmp_path, monkeypatch, capsys):
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        builder_mod, "build_artifact", lambda **kw: seen.update(kw) or _stub_manifest()
    )
    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)

    code = main(["build", str(tmp_path / "src"), str(tmp_path / "dist")])

    assert code == 0
    assert seen["source"] == tmp_path / "src"
    assert seen["out"] == tmp_path / "dist"
    assert seen["builder_id"] == _BUILDER
    # The author needs to see what they just published, in their CI log.
    assert "wafer-history" in capsys.readouterr().out


def test_build_tool_refuses_to_run_outside_a_builder_image(tmp_path, monkeypatch, capsys):
    # Without the identity there is no ABI anchor, and the platform would have
    # no way to refuse a bundle built against the wrong base.
    monkeypatch.delenv("TOOL_BUILDER_ID", raising=False)

    code = main(["build", str(tmp_path / "src"), str(tmp_path / "dist")])

    assert code == 2
    assert "TOOL_BUILDER_ID" in capsys.readouterr().err


def test_build_tool_turns_a_refused_artifact_into_a_red_build(tmp_path, monkeypatch, capsys):
    def boom(**_kw):
        raise SmokeFailed("`launch trend` exited 3")

    monkeypatch.setattr(builder_mod, "build_artifact", boom)
    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)

    code = main(["build", str(tmp_path / "src"), str(tmp_path / "dist")])

    assert code == 1
    assert "exited 3" in capsys.readouterr().err


def test_smoke_is_available_as_its_own_command_for_a_local_check(tmp_path, monkeypatch):
    seen: list[Path] = []
    monkeypatch.setattr(builder_mod, "smoke", seen.append)

    assert main(["smoke", str(tmp_path / "dist")]) == 0
    assert seen == [tmp_path / "dist"]


def test_an_unknown_verb_is_a_usage_error(capsys):
    assert main(["publish"]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_a_failing_local_smoke_is_also_a_red_exit(tmp_path, monkeypatch, capsys):
    def boom(_dist):
        raise SmokeFailed("`launch` exited 3")

    monkeypatch.setattr(builder_mod, "smoke", boom)

    assert main(["smoke", str(tmp_path / "dist")]) == 1
    assert "exited 3" in capsys.readouterr().err

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
import os
import tarfile
from dataclasses import replace
from datetime import date
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

_SOURCE = "https://gitlab.example/api/v4/projects/rca%2Fwafer-history/"

_BUILDER = "registry.example/tool-builder@sha256:beef"


def _source(
    tmp_path: Path,
    *,
    name: str = "wafer-history",
    version: str = "1.4.2",
    authors: str = "",
    description: str = "",
) -> Path:
    src = tmp_path / "src-tree"
    (src / "src").mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n{authors}{description}'
        f'\n[project.scripts]\n{name} = "pkg.cli:main"\n'
    )
    (src / "uv.lock").write_text("# pinned")
    return src


def _fake_bundle(commands: dict[str, str], *, packages: dict[str, int] | None = None):
    """Stand in for `prebuild.build_package`: lay down the bundle shape it
    produces, without the venv it takes minutes to build.

    `packages` installs site-packages entries of a given size, for the tests
    about weight. The bytes are random so they survive compression — a bundle
    of zeros would shrink to nothing and measure the wrong thing."""

    def build(*, name: str, source: Path, dst: Path) -> None:  # noqa: ARG001
        dst.mkdir(parents=True, exist_ok=True)
        for package, size in (packages or {}).items():
            installed = dst / ".venv" / "lib" / "python3.12" / "site-packages" / package
            installed.mkdir(parents=True)
            (installed / "data.bin").write_bytes(os.urandom(size))
        # A bundle always ships its own interpreter; the doubles model that
        # now, because the MCP entry point takes its version from it.
        (dst / "python" / "bin").mkdir(parents=True, exist_ok=True)
        (dst / "python" / "bin" / "python3.12").write_text("#!/bin/sh\n")
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
    assert published.author is None  # this source tree declares none
    # The sha the platform will gate on has to describe the bytes shipped
    # beside it, or nothing downstream can be trusted.
    assert published.bundle.sha256 == hashlib.sha256(bundle).hexdigest()
    assert published.bundle.size == len(bundle)
    assert [c.name for c in published.commands] == ["trend"]
    assert published.commands[0].description == "Yield trend for a lot."


def test_build_artifact_publishes_the_author_beside_the_version(tmp_path: Path) -> None:
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=_source(
            tmp_path, authors='authors = [{name = "Wafer Team", email = "wafer@example.com"}]\n'
        ),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.author == "Wafer Team <wafer@example.com>"
    assert parse_manifest((out / MANIFEST_NAME).read_bytes()).author == manifest.author


def test_build_artifact_publishes_what_the_author_says_their_tool_is(tmp_path: Path) -> None:
    """Read from ``[project].description``, which PEP 621 already gives every
    author — the same trick #724 used for the author, and for the same reason:
    the file they must edit to cut a release is the file we read, so there is
    nothing new to learn and nothing extra to remember."""
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=_source(tmp_path, description='description = "晶圓路徑與良率歷史查詢。"\n'),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.description == "晶圓路徑與良率歷史查詢。"
    assert parse_manifest((out / MANIFEST_NAME).read_bytes()).description == manifest.description


def test_an_author_who_describes_nothing_publishes_a_manifest_that_says_nothing(
    tmp_path: Path,
) -> None:
    """Encouraged, never required. A build must not fail over a courtesy."""
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.description is None


# ── #763: the author's `env.json` has to reach the MANIFEST ──────────────
#
# The host reads `manifest.env` and never reads `env.json` out of the bundle
# it unpacks, so a declaration that only lands in the tarball is one the
# platform never sees. These enter through `build_artifact` on purpose: before
# them NO test anywhere carried `env` through a real `Manifest` — the line that
# renders it was uncovered — so every test of the field was grading a double,
# which is why nothing caught that the builder was not filling it in.


def test_build_artifact_publishes_the_variables_the_author_declared(tmp_path: Path) -> None:
    src = _source(tmp_path)
    (src / "env.json").write_text(
        json.dumps(
            [
                {"name": "WAFER_API_TOKEN", "description": "personal token", "required": True},
                {"name": "WAFER_API_BASE"},
            ]
        )
    )
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=src,
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.env is not None
    assert [(e.name, e.description, e.required) for e in manifest.env] == [
        ("WAFER_API_TOKEN", "personal token", True),
        ("WAFER_API_BASE", "", None),
    ]


def test_the_declaration_survives_into_the_file_the_author_publishes(tmp_path: Path) -> None:
    """Through the manifest ON DISK — that file, not the returned object, is
    what the host fetches and reads."""
    src = _source(tmp_path)
    (src / "env.json").write_text(json.dumps([{"name": "WAFER_API_TOKEN"}]))
    out = tmp_path / "dist"

    build_artifact(
        source=src,
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    published = parse_manifest((out / MANIFEST_NAME).read_bytes())
    assert published.env is not None
    assert [e.name for e in published.env] == ["WAFER_API_TOKEN"]


def test_an_author_who_says_nothing_about_variables_is_not_read_as_needing_none(
    tmp_path: Path,
) -> None:
    """Absent stays absent. Every artifact published before #750 carries no
    declaration, and turning that into "needs nothing" would be a claim the
    author never made — the panel has to be able to say "did not say"."""
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.env is None


def test_an_empty_declaration_says_looked_and_needs_nothing(tmp_path: Path) -> None:
    """The other half of the three states: `[]` is a CLAIM, and must not
    collapse into the `None` that means the author never wrote the file."""
    src = _source(tmp_path)
    (src / "env.json").write_text("[]")
    out = tmp_path / "dist"

    manifest = build_artifact(
        source=src,
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.env == ()


def test_a_malformed_declaration_fails_the_authors_own_build(tmp_path: Path) -> None:
    """Named, and HERE — the author is the one running this build and can fix
    it. A deployment can only be told about it."""
    src = _source(tmp_path)
    (src / "env.json").write_text(json.dumps({"WAFER_API_TOKEN": "not an array"}))
    out = tmp_path / "dist"

    with pytest.raises(BuildError, match="env.json"):
        build_artifact(
            source=src,
            out=out,
            builder_id=_BUILDER,
            build_bundle=_fake_bundle({"trend": "t"}),
            smoke_check=lambda _dist: None,
        )


def _build_with_env(tmp_path: Path, contents: str | None, *, name: str = "env.json"):
    """Publish an artifact whose source carries `contents` as its `env.json`."""
    src = _source(tmp_path)
    if contents is not None:
        (src / name).write_text(contents)
    return build_artifact(
        source=src,
        out=tmp_path / "dist",
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )


def test_a_declaration_that_is_not_an_array_is_refused(tmp_path: Path) -> None:
    """`{}` on its own reaches NO entry loop, so it is the only input that
    grades the array check. The first version of these tests used a NON-empty
    object, which trips the per-entry check instead and left this guard bare —
    a mutation probe deleting it kept the whole suite green."""
    with pytest.raises(BuildError, match="must be a JSON array"):
        _build_with_env(tmp_path, "{}")


def test_a_description_that_is_not_text_is_refused(tmp_path: Path) -> None:
    """Carried verbatim into a Pydantic response model downstream, where a
    `null` becomes a 500 for the whole tool picker. The author is the only
    person who can fix it and this is the only moment they are looking."""
    with pytest.raises(BuildError, match="`description` must be a string"):
        _build_with_env(tmp_path, json.dumps([{"name": "A", "description": None}]))


def test_a_required_that_is_not_a_boolean_is_refused(tmp_path: Path) -> None:
    """`"required": "yes"` would coerce to True downstream and silently mark a
    variable mandatory that the author never marked."""
    with pytest.raises(BuildError, match="`required` must be true, false or absent"):
        _build_with_env(tmp_path, json.dumps([{"name": "A", "required": "yes"}]))


def test_a_declaration_that_cannot_be_read_is_named_not_silence(tmp_path: Path) -> None:
    """The #763 symptom through a different door: an unreadable file must not
    publish as "the author said nothing" — that is the exact silence this
    change exists to end."""
    src = _source(tmp_path)
    (src / "env.json").mkdir()

    with pytest.raises(BuildError, match="could not be read"):
        build_artifact(
            source=src,
            out=tmp_path / "dist",
            builder_id=_BUILDER,
            build_bundle=_fake_bundle({"trend": "t"}),
            smoke_check=lambda _dist: None,
        )


def test_a_declaration_that_is_a_broken_symlink_is_named_not_silence(tmp_path: Path) -> None:
    """A CI checkout that did not materialise the target would otherwise
    publish silence from a build that looked completely green."""
    src = _source(tmp_path)
    (src / "env.json").symlink_to(tmp_path / "nowhere.json")

    with pytest.raises(BuildError, match="symlink"):
        build_artifact(
            source=src,
            out=tmp_path / "dist",
            builder_id=_BUILDER,
            build_bundle=_fake_bundle({"trend": "t"}),
            smoke_check=lambda _dist: None,
        )


def test_a_declaration_saved_with_a_byte_order_mark_still_builds(tmp_path: Path) -> None:
    """The docs tell a third party to hand-write this file, and a Windows
    editor adds a BOM. Refusing it buys nothing."""
    manifest = _build_with_env(tmp_path, '\ufeff[{"name": "A"}]')

    assert manifest.env is not None
    assert [e.name for e in manifest.env] == ["A"]


def test_looked_and_needs_nothing_survives_into_the_published_file(tmp_path: Path) -> None:
    """The empty state has to reach the host, not just the returned object:
    `render_manifest` writes the key only when `env is not None`, so a
    regression collapsing `()` to absent during serialisation would leave the
    object-level test green while the panel started saying "did not say"."""
    _build_with_env(tmp_path, "[]")

    body = json.loads((tmp_path / "dist" / MANIFEST_NAME).read_bytes())
    assert body["env"] == []
    assert parse_manifest((tmp_path / "dist" / MANIFEST_NAME).read_bytes()).env == ()


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


def _pyproject(tmp_path: Path, authors: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "t"\nversion = "1"\n{authors}\n[project.scripts]\nt = "p:m"\n'
    )
    return tmp_path


def test_read_project_takes_the_author_from_the_same_file_as_the_version(tmp_path: Path) -> None:
    """#724: no new place for an author to fill in. `[project].authors` is
    already in the file they must edit to cut a release."""
    src = _pyproject(tmp_path, 'authors = [{name = "Wafer Team", email = "wafer@example.com"}]\n')

    assert read_project(src).author == "Wafer Team <wafer@example.com>"


def test_read_project_names_every_author_a_tool_has(tmp_path: Path) -> None:
    src = _pyproject(
        tmp_path,
        'authors = [{name = "A", email = "a@x"}, {name = "B", email = "b@x"}]\n',
    )

    assert read_project(src).author == "A <a@x>, B <b@x>"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ('{name = "Solo"}', "Solo"),
        ('{email = "solo@x"}', "solo@x"),
        # Neither half is a way to reach anyone, so it contributes nothing
        # rather than rendering as an empty pair of brackets.
        ("{}", None),
    ],
)
def test_read_project_renders_a_half_filled_author(tmp_path: Path, entry, expected) -> None:
    src = _pyproject(tmp_path, f"authors = [{entry}]\n")

    assert read_project(src).author == expected


def test_read_project_leaves_the_author_absent_instead_of_failing_the_build(
    tmp_path: Path,
) -> None:
    """Deliberately unlike `version`, which is required. A version is release
    semantics the platform reasons about; an author is a courtesy, and refusing
    to build without one would break every tool already in the field the day
    the builder image ships."""
    src = _pyproject(tmp_path, "")

    assert read_project(src).author is None


def test_read_project_ignores_an_authors_table_it_cannot_read(tmp_path: Path) -> None:
    """A field that decides nothing must not be able to fail a build."""
    src = _pyproject(tmp_path, 'authors = "Wafer Team"\n')

    assert read_project(src).author is None


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


def test_build_tool_names_the_author_in_the_ci_log(tmp_path, monkeypatch, capsys):
    """#724: the author's own build is the first place the string is ever
    shown, so a typo surfaces to the one person who can fix it."""
    published = replace(_stub_manifest(), author="Wafer Team <wafer@example.com>")
    monkeypatch.setattr(builder_mod, "build_artifact", lambda **kw: published)
    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)

    assert main(["build", str(tmp_path / "src"), str(tmp_path / "dist")]) == 0

    out = capsys.readouterr().out
    assert "by Wafer Team <wafer@example.com>" in out
    assert "note:" not in out


def test_build_tool_says_how_to_add_a_missing_author(tmp_path, monkeypatch, capsys):
    """Nothing refuses a build over this field, so without a word here an
    author who mistyped the table would never learn that it did not publish."""
    monkeypatch.setattr(builder_mod, "build_artifact", lambda **kw: _stub_manifest())
    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)

    assert main(["build", str(tmp_path / "src"), str(tmp_path / "dist")]) == 0

    out = capsys.readouterr().out
    assert "no author published" in out
    assert "authors = [" in out


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


def _venv_shaped_bundle(commands: dict[str, str], *, interpreter: str):
    """A bundle shaped like the one `prebuild` really produces.

    The earlier doubles wrote only regular files, which is why nothing caught
    that a real build cannot be unpacked: `uv venv` leaves `.venv/bin/python`
    pointing at the ABSOLUTE path of the interpreter it was built with, and
    that path does not exist anywhere the bundle is going.
    """
    plain = _fake_bundle(commands)

    def build(*, name: str, source: Path, dst: Path) -> None:
        plain(name=name, source=source, dst=dst)
        (dst / "python" / "bin").mkdir(parents=True, exist_ok=True)
        (dst / "python" / "bin" / "python3.12").write_text("#!/bin/sh\n")
        (dst / "python" / "bin" / "python3.12").chmod(0o755)
        venv = dst / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "python").symlink_to(interpreter)
        (venv / "python3").symlink_to("python")

    return build


def test_a_bundle_built_by_uv_can_actually_be_unpacked(tmp_path: Path) -> None:
    """The build machine's interpreter path is meaningless anywhere else, so a
    bundle carrying it is not relocatable — and the safe tar filter both this
    build and the host use refuses an absolute link outright. Packing repoints
    it at the interpreter the bundle ships with."""
    out = tmp_path / "dist"

    build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_venv_shaped_bundle(
            {"trend": "t"}, interpreter="/home/someone/.local/share/uv/python/x/bin/python3.12"
        ),
        smoke_check=lambda _dist: None,
    )

    with tarfile.open(fileobj=io.BytesIO((out / BUNDLE_NAME).read_bytes())) as tar:
        links = {m.name: m.linkname for m in tar.getmembers() if m.issym()}

    assert not any(t.startswith("/") for t in links.values()), (
        f"a relocatable bundle cannot carry an absolute link: {links}"
    )
    # And it points at something real inside the bundle.
    assert links[".venv/bin/python"] == "../../python/bin/python3.12"


def test_a_link_pointing_out_of_the_bundle_fails_the_authors_build(tmp_path: Path) -> None:
    # Not something to quietly rewrite: a bundle that reaches outside itself is
    # not self-contained, and no host would unpack it. Better a red build than
    # an artifact nothing can install.
    out = tmp_path / "dist"

    with pytest.raises(BuildError, match="outside the bundle"):
        build_artifact(
            source=_source(tmp_path),
            out=out,
            builder_id=_BUILDER,
            build_bundle=_venv_shaped_bundle({"trend": "t"}, interpreter="/etc/passwd"),
            smoke_check=lambda _dist: None,
        )


def test_an_absolute_link_into_the_bundle_becomes_relative(tmp_path: Path) -> None:
    # Same target, honest spelling. The path was correct on the build machine
    # and correct nowhere else; relative, it is correct everywhere.
    out = tmp_path / "dist"
    src = _source(tmp_path)

    def build(*, name: str, source: Path, dst: Path) -> None:  # noqa: ARG001
        _fake_bundle({"trend": "t"})(name=name, source=source, dst=dst)
        (dst / "lib").mkdir()
        (dst / "lib" / "real.so").write_text("x")
        (dst / "lib" / "alias.so").symlink_to(dst / "lib" / "real.so")

    build_artifact(
        source=src,
        out=out,
        builder_id=_BUILDER,
        build_bundle=build,
        smoke_check=lambda _dist: None,
    )

    with tarfile.open(fileobj=io.BytesIO((out / BUNDLE_NAME).read_bytes())) as tar:
        links = {m.name: m.linkname for m in tar.getmembers() if m.issym()}

    assert links["lib/alias.so"] == "real.so"


def test_every_bundle_gains_an_mcp_entry_point(tmp_path: Path) -> None:
    """#674: the same tool, reachable by an engineer's own agent. The adapter
    is generic, so it is injected rather than written — an author publishing a
    tool gets the MCP face without knowing MCP exists."""
    out = tmp_path / "dist"

    build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    with tarfile.open(fileobj=io.BytesIO((out / BUNDLE_NAME).read_bytes())) as tar:
        names = {m.name: m for m in tar.getmembers()}

    assert "mcp_server.py" in names
    assert names["mcp"].mode & 0o111, "the entry point has to be runnable"


def test_a_bundle_without_an_interpreter_fails_the_build(tmp_path: Path) -> None:
    # The MCP entry point runs on the interpreter the bundle ships, so its
    # absence means the build did not finish — better to say so than to write
    # an entry point that names a python that is not there.
    def build(*, name: str, source: Path, dst: Path) -> None:  # noqa: ARG001
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "commands.json").write_text("[]")
        (dst / "schemas").mkdir()

    with pytest.raises(BuildError, match="no interpreter"):
        build_artifact(
            source=_source(tmp_path),
            out=tmp_path / "dist",
            builder_id=_BUILDER,
            build_bundle=build,
            smoke_check=lambda _dist: None,
        )


def test_the_build_publishes_the_two_files_and_nothing_else(tmp_path: Path) -> None:
    """A tool used to ship a second artifact — a Dockerfile, for an image per
    tool. That image stored the bundle a second time and, having it baked in,
    had nothing left to verify at run time.

    One runner image now fetches any tool from its URL through the same
    resolver the platform uses, so a build publishes only what the platform
    consumes."""
    out = tmp_path / "dist"

    build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert sorted(p.name for p in out.iterdir()) == sorted([BUNDLE_NAME, MANIFEST_NAME])


# ─── what a bundle may weigh (#674) ──────────────────────────────────


@pytest.fixture
def signing(monkeypatch):
    """A platform signing key that exists only for this test, wired in as the
    one this build trusts."""
    from workspace_app.tooling import grant as grant_mod

    private, public = grant_mod.keypair()
    monkeypatch.setattr(grant_mod, "TRUSTED_KEYS", {"alice": public})
    # Small enough that a few kilobytes of random bytes exceed it. The real
    # number is pinned in test_grant.py, against the figure the guide quotes.
    monkeypatch.setattr(grant_mod, "DEFAULT_MAX_BYTES", 4096)
    return private


def _grant(private, *, tool="wafer-history", mb=1, publish_until=None):
    from workspace_app.tooling.grant import Grant, issue

    return issue(
        Grant(source=_SOURCE, tool=tool, max_bytes=mb * 1024 * 1024, publish_until=publish_until),
        private_key=private,
    )


def test_a_bundle_over_the_limit_is_refused(tmp_path: Path, signing) -> None:
    """The weight is the artifact every host downloads, so the build is where
    it has to be caught: past this point it is published, cached, and pulled."""
    out = tmp_path / "dist"

    with pytest.raises(BuildError, match="limit"):
        build_artifact(
            source=_source(tmp_path),
            out=out,
            builder_id=_BUILDER,
            build_bundle=_fake_bundle({"trend": "t"}, packages={"pandas": 20_000}),
            smoke_check=lambda _dist: None,
        )


def test_nothing_is_published_when_the_bundle_is_too_big(tmp_path: Path, signing) -> None:
    """A dist holding a rejected bundle is worse than no dist: CI publishes
    whatever it finds, and the refusal would reach a user instead of the
    author."""
    out = tmp_path / "dist"

    with pytest.raises(BuildError):
        build_artifact(
            source=_source(tmp_path),
            out=out,
            builder_id=_BUILDER,
            build_bundle=_fake_bundle({"trend": "t"}, packages={"pandas": 20_000}),
            smoke_check=lambda _dist: None,
        )

    assert not (out / BUNDLE_NAME).exists()
    assert not (out / MANIFEST_NAME).exists()


def test_the_refusal_names_the_heaviest_things_so_the_author_knows_what_to_cut(
    tmp_path: Path, signing
) -> None:
    """ "Too big" on its own sends someone to guess at their dependency tree.
    The build has the tree in front of it and can say which entries account
    for the weight."""
    with pytest.raises(BuildError) as caught:
        build_artifact(
            source=_source(tmp_path),
            out=tmp_path / "dist",
            builder_id=_BUILDER,
            build_bundle=_fake_bundle(
                {"trend": "t"}, packages={"pandas": 30_000, "pytest": 9_000, "tiny": 10}
            ),
            smoke_check=lambda _dist: None,
        )

    message = str(caught.value)
    assert "pandas" in message
    assert "pytest" in message
    # Ordered heaviest first, and the trivia left out: a list of everything
    # installed is the same problem as no list at all.
    assert message.index("pandas") < message.index("pytest")
    assert "tiny" not in message


def test_a_certificate_raises_the_limit_for_the_tool_it_names(tmp_path: Path, signing) -> None:
    source = _source(tmp_path)
    (source / "tool-certificate.token").write_text(_grant(signing))

    manifest = build_artifact(
        source=source,
        out=tmp_path / "dist",
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}, packages={"pandas": 20_000}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.bundle.size > 4096


def test_the_certificate_travels_in_the_manifest(tmp_path: Path, signing) -> None:
    """So the platform checks the same certificate the author built against,
    without an operator having to go and ask for it."""
    source = _source(tmp_path)
    token = _grant(signing)
    (source / "tool-certificate.token").write_text(token)
    out = tmp_path / "dist"

    build_artifact(
        source=source,
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}, packages={"pandas": 20_000}),
        smoke_check=lambda _dist: None,
    )

    assert parse_manifest((out / MANIFEST_NAME).read_bytes()).grant == token


def test_a_tool_with_no_certificate_publishes_none(tmp_path: Path, signing) -> None:
    out = tmp_path / "dist"

    build_artifact(
        source=_source(tmp_path),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )

    assert parse_manifest((out / MANIFEST_NAME).read_bytes()).grant is None


def test_the_build_uses_whatever_certificate_it_was_given(tmp_path: Path, signing) -> None:
    """It has nothing to check the certificate against. The name in one is the
    PLATFORM's name for the tool, and a build only knows the one in
    `[project.scripts]` — so binding here could never pass, and the attempt
    silently cost the author their whole allowance.

    Using it unchecked is safe because the author's runner was never a
    boundary: `verify` and the host both refuse a certificate that names a
    different tool, and they are the ones deciding what runs."""
    source = _source(tmp_path)
    (source / "tool-certificate.token").write_text(_grant(signing, tool="something-else", mb=1))

    manifest = build_artifact(
        source=source,
        out=tmp_path / "dist",
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}, packages={"pandas": 20_000}),
        smoke_check=lambda _dist: None,
    )

    assert manifest.bundle.size > 4096  # the 1MB allowance applied


def test_an_expired_certificate_says_it_expired_rather_than_too_big(
    tmp_path: Path, signing, monkeypatch
) -> None:
    """Past the deadline the allowance is gone, so the build refuses again —
    and says the allowance ran out rather than "too big", because the two need
    different actions: one is deleting dependencies, the other is asking us."""
    from workspace_app.tooling import builder as mod

    source = _source(tmp_path)
    (source / "tool-certificate.token").write_text(_grant(signing, publish_until=date(2026, 1, 1)))
    monkeypatch.setattr(mod, "_today", lambda: date(2026, 8, 1))

    with pytest.raises(BuildError, match="publish until"):
        build_artifact(
            source=source,
            out=tmp_path / "dist",
            builder_id=_BUILDER,
            build_bundle=_fake_bundle({"trend": "t"}, packages={"pandas": 20_000}),
            smoke_check=lambda _dist: None,
        )


def test_weighing_a_bundle_that_carries_no_interpreter(tmp_path: Path) -> None:
    """`_heaviest` runs on whatever the build produced, including a tree that
    never got its interpreter because the build failed earlier. It reports
    what is there rather than raising on what is not — a diagnostic that dies
    while explaining a failure explains nothing."""
    bundle = tmp_path / "b"
    site = bundle / ".venv" / "lib" / "python3.12" / "site-packages" / "pandas"
    site.mkdir(parents=True)
    (site / "data.bin").write_bytes(os.urandom(1000))

    assert builder_mod._heaviest(bundle) == [("pandas", 1000)]


def test_a_relative_symlink_out_of_the_bundle_fails_the_build(tmp_path: Path) -> None:
    """The check only looked at absolute links. A relative one that climbs out
    packs fine and is then refused by the host's `data` filter — which is the
    failure this whole step exists to move forward, from a stranger's machine
    to the author's own build."""
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "launch").write_text("#!/bin/sh\n")
    (bundle / "escape").symlink_to("../../../etc/passwd")

    with pytest.raises(BuildError, match="escape"):
        builder_mod.pack_bundle(bundle)

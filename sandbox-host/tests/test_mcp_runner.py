"""One runner image, any tool, fetched from its artifact URL (#674).

The platform runs a tool by resolving its artifact URL, gating the builder it
was built with, checking the sha and unpacking into a content-addressed cache.
An engineer running the same tool through their own agent should get the same
treatment — so the runner is that machinery with the sandbox taken away, and
the tool arrives by URL rather than baked into an image per tool.

What that buys, beyond not storing the bundle twice: the bundle an engineer
runs is *verified at run time*, by the same code and the same gates as the
platform's. An image with the bundle copied in has nothing left to check.
"""

from __future__ import annotations

import hashlib
import io
import os
import json
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from sandbox_host.mcp_runner import main

_MANIFEST_URL = "https://gitlab.example/api/v4/projects/7/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool"
_BUILDER = "registry.example/tool-builder@sha256:beef"


def _bundle(body: bytes = b"#!/bin/sh\necho hi\n") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("launch", "mcp"):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _manifest(data: bytes, **over: object) -> bytes:
    body: dict[str, object] = {
        "format_version": 1,
        "name": "wafer-history",
        "version": "1.4.2",
        "commands": [
            {
                "name": "trend",
                "description": "Yield trend for a lot.",
                "params_json_schema": {"type": "object", "properties": {}},
            }
        ],
        "builder": _BUILDER,
        "python": "3.12",
        "arch": "x86_64",
        "bundle": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
    }
    body.update(over)
    return json.dumps(body).encode()


class _Wire:
    def __init__(self, **pages: bytes) -> None:
        self.pages = dict(pages)
        self.asked: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        path = urlsplit(url).path
        key = "manifest" if path.endswith("tool.manifest.json") else "bundle"
        if key not in self.pages:
            raise OSError(f"{url} is unreachable")
        return self.pages[key]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)
    monkeypatch.setenv("TOOL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    return tmp_path


def test_the_runner_resolves_the_url_and_hands_over_to_the_bundle(env) -> None:
    data = _bundle()
    handed: list[Path] = []

    code = main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=handed.append,
    )

    assert code == 0
    # The bundle's own MCP entry point, out of the content-addressed cache —
    # the same layout the host mounts into a sandbox.
    (entry,) = handed
    assert entry.name == "mcp"
    assert hashlib.sha256(data).hexdigest() in str(entry)
    assert entry.is_file()


def test_nothing_reaches_stdout_before_the_handover(env, capsys) -> None:
    """stdout is the JSON-RPC channel. One stray line — a progress note, a
    warning — and the client fails to parse the first message, which presents
    as "the tool is broken" rather than as our own output."""
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=lambda _entry: None,
    )

    assert capsys.readouterr().out == ""


def test_what_was_resolved_is_reported_on_stderr(env, capsys) -> None:
    # Which version an engineer is actually running is the first thing they
    # ask when a tool behaves differently from the platform.
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=lambda _entry: None,
    )

    err = capsys.readouterr().err
    assert "wafer-history" in err and "1.4.2" in err


def test_a_second_run_serves_from_the_cache_instead_of_downloading_again(env) -> None:
    """The reason the bundle is not stored in an image: it is already stored
    once, in the artifact store, and once more here keyed by its sha. A second
    start fetches the manifest to see whether the sha moved, and stops there."""
    data = _bundle()
    wire = _Wire(manifest=_manifest(data), bundle=data)
    args = ["wafer-history", _MANIFEST_URL]

    def downloads() -> list[str]:
        # Match on the PATH: the artifact URL carries `?job=…` after the
        # filename, which is the trap `bundle_url` exists to avoid.
        return [u for u in wire.asked if urlsplit(u).path.endswith("tool.tar.gz")]

    main(args, fetch=wire, hand_over=lambda _e: None)
    after_first = downloads()
    main(args, fetch=wire, hand_over=lambda _e: None)

    assert len(after_first) == 1
    assert downloads() == after_first


def test_an_artifact_this_platform_would_refuse_is_refused_here_too(env, capsys) -> None:
    """The same gate as the platform's. A bundle built on another base carries
    an interpreter that will not load here, and "it segfaulted" is the worst
    possible way to find that out."""
    data = _bundle()

    code = main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data, builder="somewhere/else:v1"), bundle=data),
        hand_over=lambda _e: pytest.fail("must not hand over"),
    )

    assert code == 1
    assert "builder" in capsys.readouterr().err.lower()


def test_a_url_serving_a_different_tool_is_refused(env, capsys) -> None:
    # The name in the config is what the engineer's agent will call this
    # server; a URL that quietly starts serving something else would put
    # another tool's commands behind that name.
    data = _bundle()

    code = main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data, name="pdf-extract"), bundle=data),
        hand_over=lambda _e: pytest.fail("must not hand over"),
    )

    assert code == 1
    assert "pdf-extract" in capsys.readouterr().err


def test_an_unreachable_artifact_is_reported_readably(env, capsys) -> None:
    code = main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(),
        hand_over=lambda _e: pytest.fail("must not hand over"),
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "wafer-history" in err


def test_a_corrupted_bundle_never_reaches_the_agent(env, capsys) -> None:
    """The sha check, which the per-tool image had no way to perform: it
    shipped whatever was copied in."""
    data = _bundle()

    code = main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data + b"tampered"),
        hand_over=lambda _e: pytest.fail("must not hand over"),
    )

    assert code == 1
    assert "Traceback" not in capsys.readouterr().err


def test_usage_is_printed_when_the_arguments_are_wrong(env, capsys) -> None:
    assert main([_MANIFEST_URL], fetch=_Wire(), hand_over=lambda _e: None) == 2

    err = capsys.readouterr().err
    assert "tool.manifest.json" in err


def test_a_runner_without_a_builder_identity_refuses_to_run(env, monkeypatch, capsys) -> None:
    """The ABI anchor is baked into the image. Missing, there is nothing to
    check a bundle against, and running one anyway is how a segfault happens."""
    monkeypatch.delenv("TOOL_BUILDER_ID")

    code = main(["wafer-history", _MANIFEST_URL], fetch=_Wire(), hand_over=lambda _e: None)

    assert code == 2
    assert "TOOL_BUILDER_ID" in capsys.readouterr().err


def test_the_note_on_stderr_claims_only_what_is_known(env, capsys) -> None:
    """It used to say "from cache" on every run, including the one that had
    just downloaded. A line that is right half the time gets believed."""
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=lambda _e: None,
    )

    assert "cache" not in capsys.readouterr().err


def test_a_local_runner_never_serves_a_copy_it_could_not_confirm(env, capsys) -> None:
    """The host serves last-known-good through an outage, because one artifact
    store being down should not stop every workspace. A runner on someone's
    laptop is the opposite case, and the reason is revocation.

    Access to a tool is managed where the artifact lives. Take someone's read
    access away and their next start should fail — but a resolver that falls
    back to its cache would keep running the tool it already had, for as long
    as that machine lives, and nothing we do could reach it. GitLab also
    answers 404 for a project you may not see, which is indistinguishable
    from an expired artifact, so the status code cannot make this decision.

    So locally: confirm it today, or do not run it."""
    data = _bundle()
    args = ["wafer-history", _MANIFEST_URL]

    main(args, fetch=_Wire(manifest=_manifest(data), bundle=data), hand_over=lambda _e: None)
    capsys.readouterr()

    code = main(
        args,
        fetch=_Wire(),  # the artifact store now refuses, or is gone
        hand_over=lambda _e: pytest.fail("a cached copy must not be served"),
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "wafer-history" in err


# ─── where a tool's files go ─────────────────────────────────────────


def test_nothing_mounted_is_detected_for_a_path_that_is_not_there() -> None:
    from sandbox_host.mcp_runner import _nothing_mounted_at

    assert _nothing_mounted_at("/definitely/not/mounted/anywhere")


def test_an_unmounted_workspace_is_called_out_before_the_tool_runs(
    env, capsys, monkeypatch
) -> None:
    """Reads fail loudly when nothing is mounted — "no such file". Writes do
    not: the tool succeeds, reports a path, and the file goes with the
    container. The agent then tells someone their download is ready and it is
    nowhere on their disk.

    Measured: `docker run` with no `-v …:/work` wrote the file into the
    container layer and it was gone on exit."""
    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: True)
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=lambda _e: None,
    )

    err = capsys.readouterr().err
    assert "/work" in err
    assert "lost" in err.lower()


def test_a_mounted_workspace_says_nothing(env, capsys, monkeypatch) -> None:
    # The normal case. A warning on every start is a warning nobody reads.
    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: False)
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=lambda _e: None,
    )

    assert "lost" not in capsys.readouterr().err.lower()


def test_the_runner_becomes_the_owner_of_the_workspace_it_was_given(env, monkeypatch) -> None:
    """The config an engineer pastes must contain nothing about their machine.

    `--user "$(id -u):$(id -g)"` cannot be written in an MCP config — clients
    expand environment variables at best, never command substitution — so
    asking for it means every person edits the snippet with their own uid, and
    whoever forgets gets root-owned files in their own project.

    The runner already reads that uid to warn about it. Becoming it instead
    removes the question, and matches the platform, where a tool runs as an
    unprivileged uid rather than as root."""
    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: False)
    monkeypatch.setattr(mcp_runner, "_workspace_owner", lambda _p: (1000, 1000))
    monkeypatch.setattr(mcp_runner.os, "geteuid", lambda: 0)
    became: list[tuple[int, int]] = []
    order: list[str] = []
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        become=lambda uid, gid: (became.append((uid, gid)), order.append("became")),
        hand_over=lambda _e: order.append("handed over"),
    )

    assert became == [(1000, 1000)]
    # Before the handover, which replaces the process: after it there is no
    # "after".
    assert order == ["became", "handed over"]


def test_a_runner_already_running_as_a_person_stays_as_it_is(env, monkeypatch) -> None:
    # Someone who passed `--user` themselves. Dropping again is not possible
    # and not wanted.
    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: False)
    monkeypatch.setattr(mcp_runner, "_workspace_owner", lambda _p: (1000, 1000))
    monkeypatch.setattr(mcp_runner.os, "geteuid", lambda: 1000)
    became: list[tuple[int, int]] = []
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        become=lambda uid, gid: became.append((uid, gid)),
        hand_over=lambda _e: None,
    )

    assert became == []


def test_a_workspace_owned_by_root_is_left_alone(env, monkeypatch) -> None:
    """Rootless docker: the process is uid 0 and so is the mounted directory,
    because the person outside maps to root inside. Files already land owned
    by them, and dropping to 0 would be a no-op dressed up as a decision."""
    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: False)
    monkeypatch.setattr(mcp_runner, "_workspace_owner", lambda _p: (0, 0))
    monkeypatch.setattr(mcp_runner.os, "geteuid", lambda: 0)
    became: list[tuple[int, int]] = []
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        become=lambda uid, gid: became.append((uid, gid)),
        hand_over=lambda _e: None,
    )

    assert became == []


def test_nothing_mounted_means_nobody_to_become(env, monkeypatch) -> None:
    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: True)
    monkeypatch.setattr(mcp_runner.os, "geteuid", lambda: 0)
    became: list[tuple[int, int]] = []
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        become=lambda uid, gid: became.append((uid, gid)),
        hand_over=lambda _e: None,
    )

    assert became == []


def test_the_skill_explains_the_warning_this_runner_prints(env, capsys, monkeypatch) -> None:
    """The skill turns what someone SEES into what they should DO, so the two
    have to agree. Compared against what the runner actually PRINTS, not
    against its source: the message is assembled at runtime, and a test
    matching source text would pass while the wording drifted.

    Nothing else would notice. The person would search the skill for their
    error and find nothing."""
    from pathlib import Path

    from sandbox_host import mcp_runner

    monkeypatch.setattr(mcp_runner, "_nothing_mounted_at", lambda _p: True)
    data = _bundle()

    main(
        ["wafer-history", _MANIFEST_URL],
        fetch=_Wire(manifest=_manifest(data), bundle=data),
        hand_over=lambda _e: None,
    )

    warning = capsys.readouterr().err.splitlines()[-1]
    # The distinctive half of the sentence, before the advice that follows it.
    printed = warning.split(",")[0].removeprefix("warning: ")
    skill = (Path(__file__).resolve().parents[2] / "tool-skill" / "SKILL.md").read_text("utf-8")

    assert printed in skill, f"the skill does not explain what the runner prints: {printed!r}"


def test_the_workspace_owner_is_read_from_the_directory(tmp_path) -> None:
    """The seam every other test replaces. It decides whether the runner drops
    privileges, so the real thing has to be exercised somewhere."""
    from sandbox_host.mcp_runner import _workspace_owner

    assert _workspace_owner(str(tmp_path)) == (os.getuid(), os.getgid())
    assert _workspace_owner("/definitely/not/here") is None

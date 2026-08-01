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


def test_a_stale_answer_says_the_artifact_store_was_unreachable(env, capsys) -> None:
    """Serving the last version that resolved keeps an engineer working
    through an outage, but silently serving yesterday's tool is how someone
    spends an afternoon on a bug that was fixed this morning."""
    data = _bundle()
    args = ["wafer-history", _MANIFEST_URL]

    main(args, fetch=_Wire(manifest=_manifest(data), bundle=data), hand_over=lambda _e: None)
    capsys.readouterr()
    code = main(args, fetch=_Wire(), hand_over=lambda _e: None)

    assert code == 0
    assert "unreachable" in capsys.readouterr().err


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

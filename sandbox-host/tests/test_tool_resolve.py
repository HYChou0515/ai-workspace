"""P6 — turning a tool's artifact URL into something mountable (#674).

The app never talks to GitLab: it asks the host to resolve a URL, and the host
answers with the sha it installed plus the command metadata the model needs.
Keeping the credential on one side is the reason this endpoint exists at all,
and answering with the metadata is what stops the app's idea of a tool's
arguments drifting from the bundle that will actually run.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from sandbox_host.artifact import ArtifactError, IncompatibleArtifact
from sandbox_host.tool_cache import ToolCache
from sandbox_host.tool_resolve import FetchError, ToolResolver, bundle_url

_MANIFEST_URL = "https://gitlab.example/api/v4/projects/7/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool"
_BUILDER = "registry.example/tool-builder@sha256:beef"


def _bundle(body: bytes = b"#!/bin/sh\n") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("launch")
        info.size = len(body)
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
    """A stand-in for GitLab: URL -> bytes, remembering what was asked for."""

    def __init__(self, **pages: bytes) -> None:
        self.pages = dict(pages)
        self.asked: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        # Match on the PATH: the artifact URL carries `?job=…`, which is
        # exactly the trap `bundle_url` exists to avoid.
        path = urlsplit(url).path
        key = "manifest" if path.endswith("tool.manifest.json") else "bundle"
        try:
            return self.pages[key]
        except KeyError as exc:
            raise FetchError(f"{url} is unreachable") from exc


def _resolver(tmp_path: Path, wire: _Wire) -> ToolResolver:
    return ToolResolver(
        ToolCache(tmp_path / "ext", harden=lambda _p: None),
        builder_id=_BUILDER,
        arch="x86_64",
        fetch=wire,
        state_dir=tmp_path,
    )


def test_resolve_installs_the_bundle_and_answers_with_what_the_model_needs(
    tmp_path: Path,
) -> None:
    data = _bundle()
    wire = _Wire(manifest=_manifest(data), bundle=data)

    resolved = _resolver(tmp_path, wire).resolve("wafer-history", _MANIFEST_URL)

    assert resolved.sha == hashlib.sha256(data).hexdigest()
    assert resolved.version == "1.4.2"
    assert resolved.stale is False
    assert [c.name for c in resolved.commands] == ["trend"]
    # Installed and runnable, under its own sha.
    assert (tmp_path / "ext" / resolved.sha / "launch").exists()


def test_the_bundle_url_is_the_manifest_url_with_the_filename_swapped() -> None:
    # An author hands over ONE url. GitLab's artifact endpoint carries the job
    # in a query parameter, so a naive string replace over the whole URL would
    # be wrong the moment a job is named after the file.
    assert bundle_url(_MANIFEST_URL) == (
        "https://gitlab.example/api/v4/projects/7/jobs/artifacts/main/raw/dist/"
        "tool.tar.gz?job=build-tool"
    )


def test_a_cached_sha_is_not_downloaded_again(tmp_path: Path) -> None:
    # 150MB per bundle: the second sandbox on this host must pay a manifest
    # fetch, not another download.
    data = _bundle()
    wire = _Wire(manifest=_manifest(data), bundle=data)
    resolver = _resolver(tmp_path, wire)
    resolver.resolve("wafer-history", _MANIFEST_URL)
    wire.asked.clear()

    resolver.resolve("wafer-history", _MANIFEST_URL)

    assert [urlsplit(u).path.rsplit("/", 1)[-1] for u in wire.asked] == ["tool.manifest.json"]


def test_a_bundle_built_for_another_base_is_refused_before_it_is_downloaded(
    tmp_path: Path,
) -> None:
    data = _bundle()
    wire = _Wire(manifest=_manifest(data, builder="registry.example/other@sha256:old"))

    with pytest.raises(IncompatibleArtifact):
        _resolver(tmp_path, wire).resolve("wafer-history", _MANIFEST_URL)

    # Not merely unmounted — never fetched. The gate is there to avoid paying
    # for, and storing, something that could never run.
    assert len(wire.asked) == 1
    assert not (tmp_path / "ext").exists()


def test_bytes_that_do_not_match_the_manifest_are_not_installed(tmp_path: Path) -> None:
    wire = _Wire(manifest=_manifest(_bundle()), bundle=_bundle(b"something else\n"))

    with pytest.raises(ArtifactError):
        _resolver(tmp_path, wire).resolve("wafer-history", _MANIFEST_URL)

    assert list((tmp_path / "ext").iterdir()) == [] if (tmp_path / "ext").exists() else True


def test_an_unreachable_store_serves_the_last_version_that_worked(tmp_path: Path) -> None:
    # An artifact-store outage must not take every workspace down with it.
    data = _bundle()
    resolver = _resolver(tmp_path, _Wire(manifest=_manifest(data), bundle=data))
    first = resolver.resolve("wafer-history", _MANIFEST_URL)

    offline = _resolver(tmp_path, _Wire())  # nothing reachable
    fallen_back = offline.resolve("wafer-history", _MANIFEST_URL)

    assert fallen_back.sha == first.sha
    assert fallen_back.commands == first.commands
    assert fallen_back.version == "1.4.2"
    # Marked, so the app can say "this is not the latest" rather than imply it.
    assert fallen_back.stale is True


def test_an_unreachable_store_with_nothing_cached_says_which_tool_failed(
    tmp_path: Path,
) -> None:
    with pytest.raises(FetchError) as exc:
        _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)

    assert "wafer-history" in str(exc.value)


def test_a_url_that_is_not_a_manifest_is_rejected_with_the_reason() -> None:
    with pytest.raises(FetchError, match="tool.manifest.json"):
        bundle_url("https://gitlab.example/raw/dist/tool.tar.gz?job=build-tool")


def test_a_host_without_an_abi_anchor_runs_no_tool_store(monkeypatch, tmp_path) -> None:
    # Fetching without the ability to gate would mean mounting a bundle built
    # for another base and letting it fail in front of a user. Refusing to
    # have a store at all is the safe direction, and the endpoint reports it.
    from sandbox_host.config import SandboxHostSettings
    from sandbox_host.service import build_tool_resolver

    settings = SandboxHostSettings(tools_dir=str(tmp_path))

    monkeypatch.delenv("TOOL_BUILDER_ID", raising=False)
    assert build_tool_resolver(settings) is None

    monkeypatch.setenv("TOOL_BUILDER_ID", "tool-builder:2026.07")
    assert build_tool_resolver(settings) is not None


def test_a_host_with_no_tools_dir_runs_no_tool_store(monkeypatch) -> None:
    from sandbox_host.config import SandboxHostSettings
    from sandbox_host.service import build_tool_resolver

    monkeypatch.setenv("TOOL_BUILDER_ID", "tool-builder:2026.07")

    assert build_tool_resolver(SandboxHostSettings(tools_dir=None)) is None


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


def test_the_real_fetch_carries_the_hosts_token(monkeypatch) -> None:
    import urllib.request

    from sandbox_host.tool_resolve import TOKEN_ENV, _http_get

    seen: list[urllib.request.Request] = []
    monkeypatch.setenv(TOKEN_ENV, "glpat-secret")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: seen.append(req) or _Response(b"ok")
    )

    assert _http_get("https://gitlab.example/m") == b"ok"
    assert seen[0].get_header("Private-token") == "glpat-secret"


def test_a_404_says_the_thing_the_operator_actually_needs_to_hear(monkeypatch) -> None:
    # R5: the overwhelmingly likely cause of a 404 here is an expired GitLab
    # artifact, and the fix is one line in the author's CI file. A bare
    # "404 Not Found" sends someone hunting through the wrong system.
    import urllib.error
    import urllib.request

    from sandbox_host.tool_resolve import _http_get

    def boom(_req, timeout):
        raise urllib.error.HTTPError("https://g/m", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(FetchError) as exc:
        _http_get("https://gitlab.example/m")

    assert "expire_in: never" in str(exc.value)


def test_an_unreachable_host_is_a_fetch_error_not_a_stray_oserror(monkeypatch) -> None:
    import urllib.request

    from sandbox_host.tool_resolve import _http_get

    def boom(_req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    with pytest.raises(FetchError, match="unreachable"):
        _http_get("https://gitlab.example/m")


def test_the_fetch_works_without_a_token_for_a_public_project(monkeypatch) -> None:
    import urllib.request

    from sandbox_host.tool_resolve import TOKEN_ENV, _http_get

    monkeypatch.delenv(TOKEN_ENV, raising=False)
    seen: list[urllib.request.Request] = []
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: seen.append(req) or _Response(b"ok")
    )

    assert _http_get("https://gitlab.example/m") == b"ok"
    assert seen[0].get_header("Private-token") is None

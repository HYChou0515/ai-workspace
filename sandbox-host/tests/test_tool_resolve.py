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

from .conftest import certify

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
        # Certified, because that is what an artifact is: a tool the
        # platform admitted. Tests that want a refusal pass `grant=None`.
        "grant": certify("wafer-history"),
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


def _patch_opener(monkeypatch, respond):
    """Replace what `_http_get` actually calls.

    Not `urlopen`: the fetch goes through an opener carrying the redirect
    handler that re-decides the credential on every hop, so a double wired to
    `urlopen` would model a request path the code no longer takes."""
    from sandbox_host import tool_resolve

    class _Opener:
        def open(self, request, timeout=None):  # noqa: ARG002
            return respond(request)

    # On the module that USES it:  bound the name at import, so
    # patching it where it is defined would reach nothing.
    monkeypatch.setattr(tool_resolve, "artifact_opener", _Opener)


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


def test_resolve_carries_the_author_through_to_the_app(tmp_path: Path) -> None:
    """#724: the app cannot read the manifest itself (that is the whole point
    of resolving here), so anything a person needs from it has to come back in
    this answer or it does not exist downstream."""
    data = _bundle()
    wire = _Wire(manifest=_manifest(data, author="Wafer Team <wafer@example.com>"), bundle=data)

    resolved = _resolver(tmp_path, wire).resolve("wafer-history", _MANIFEST_URL)

    assert resolved.author == "Wafer Team <wafer@example.com>"


def test_a_manifest_with_no_author_resolves_to_no_author(tmp_path: Path) -> None:
    data = _bundle()
    wire = _Wire(manifest=_manifest(data), bundle=data)

    assert _resolver(tmp_path, wire).resolve("wafer-history", _MANIFEST_URL).author is None


def test_the_remembered_copy_keeps_the_author(tmp_path: Path) -> None:
    """An outage is exactly when someone asks who to contact, so provenance
    must not be the thing that disappears with the artifact store."""
    data = _bundle()
    resolver = _resolver(
        tmp_path, _Wire(manifest=_manifest(data, author="Wafer Team <w@x>"), bundle=data)
    )
    resolver.resolve("wafer-history", _MANIFEST_URL)

    fallen_back = _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)

    assert fallen_back.stale is True
    assert fallen_back.author == "Wafer Team <w@x>"


def test_a_note_written_before_authors_existed_still_falls_back(tmp_path: Path) -> None:
    """`last-known-good.json` outlives the host that wrote it — an upgrade must
    not turn every remembered tool into a KeyError during an outage, which is
    the one moment the file exists for."""
    data = _bundle()
    resolver = _resolver(tmp_path, _Wire(manifest=_manifest(data), bundle=data))
    resolver.resolve("wafer-history", _MANIFEST_URL)
    note = tmp_path / "last-known-good.json"
    body = json.loads(note.read_text())
    for entry in body.values():
        entry.pop("author", None)
    note.write_text(json.dumps(body))

    fallen_back = _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)

    assert fallen_back.stale is True
    assert fallen_back.author is None


def test_the_remembered_copy_keeps_what_the_tool_says_it_is(tmp_path: Path) -> None:
    """Same rule as the author, one field over. An outage is not a reason for a
    tool to stop being able to say what it is: the note is what the app is
    served from while the store is unreachable, so anything it does not carry
    is a blank the agent and the picker cannot fill from anywhere else."""
    data = _bundle()
    resolver = _resolver(
        tmp_path,
        _Wire(manifest=_manifest(data, description="晶圓路徑與良率歷史查詢。"), bundle=data),
    )
    resolver.resolve("wafer-history", _MANIFEST_URL)

    fallen_back = _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)

    assert fallen_back.stale is True
    assert fallen_back.description == "晶圓路徑與良率歷史查詢。"


def test_a_note_written_before_descriptions_existed_still_falls_back(tmp_path: Path) -> None:
    """The same upgrade hazard the author's note has: a file written by an older
    host must not raise during the one event it exists for."""
    data = _bundle()
    resolver = _resolver(tmp_path, _Wire(manifest=_manifest(data), bundle=data))
    resolver.resolve("wafer-history", _MANIFEST_URL)
    note = tmp_path / "last-known-good.json"
    body = json.loads(note.read_text())
    for entry in body.values():
        entry.pop("description", None)
    note.write_text(json.dumps(body))

    fallen_back = _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)

    assert fallen_back.stale is True
    assert fallen_back.description is None


def test_an_unreachable_store_with_nothing_cached_says_which_tool_failed(
    tmp_path: Path,
) -> None:
    with pytest.raises(FetchError) as exc:
        _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)

    assert "wafer-history" in str(exc.value)


def test_a_url_that_is_not_a_manifest_is_rejected_with_the_reason() -> None:
    #  since the rule moved into the shared contract — the same
    # one  applies, so the two gates cannot disagree about what a tool
    # URL is. Both are , which is what callers handle.
    from sandbox_host.artifact import ManifestError

    with pytest.raises(ManifestError, match="tool.manifest.json"):
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

    from sandbox_host.tool_resolve import HOSTS_ENV, TOKEN_ENV, _http_get

    seen: list[urllib.request.Request] = []
    monkeypatch.setenv(TOKEN_ENV, "glpat-secret")
    # Where it is allowed to go. Unset, it goes nowhere — see the tests below.
    monkeypatch.setenv(HOSTS_ENV, "gitlab.example")
    _patch_opener(monkeypatch, lambda req: seen.append(req) or _Response(b"ok"))

    assert _http_get("https://gitlab.example/m") == b"ok"
    assert seen[0].get_header("Private-token") == "glpat-secret"


def test_a_404_says_the_thing_the_operator_actually_needs_to_hear(monkeypatch) -> None:
    # R5: the overwhelmingly likely cause of a 404 here is an expired GitLab
    # artifact, and the fix is one line in the author's CI file. A bare
    # "404 Not Found" sends someone hunting through the wrong system.
    import urllib.error
    import urllib.request

    from sandbox_host.tool_resolve import _http_get

    def boom(_req):
        raise urllib.error.HTTPError("https://g/m", 404, "Not Found", {}, None)

    _patch_opener(monkeypatch, boom)

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
    _patch_opener(monkeypatch, lambda req: seen.append(req) or _Response(b"ok"))

    assert _http_get("https://gitlab.example/m") == b"ok"
    assert seen[0].get_header("Private-token") is None


def test_the_grant_module_is_a_verbatim_copy_of_the_apps() -> None:
    """Like `artifact.py`: the rule that admits a tool must mean the same
    thing where an artifact is built, where it is registered, and where it
    runs. Two copies that drift are three different platforms."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    app = repo / "src" / "workspace_app" / "tooling" / "grant.py"
    host = repo / "sandbox-host" / "src" / "sandbox_host" / "grant.py"

    assert host.read_bytes() == app.read_bytes(), (
        "sandbox-host/src/sandbox_host/grant.py has drifted from the app's copy — "
        "copy it across; it depends only on the stdlib and cryptography exactly "
        "so this can stay a verbatim copy"
    )


# ─── where the credential may go (#674) ──────────────────────────────


def _sent(url: str, monkeypatch) -> dict[str, str]:
    """The headers a fetch of `url` would actually put on the wire."""

    from sandbox_host import tool_resolve

    seen: dict[str, str] = {}

    class _Response:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def urlopen(request):
        seen.update(request.headers)
        return _Response()

    _patch_opener(monkeypatch, urlopen)
    tool_resolve._http_get(url)
    return seen


def test_the_credential_goes_only_where_the_deployment_says(monkeypatch) -> None:
    """The certificate cannot protect this. It is read from the manifest, and
    the manifest is what the request was for — so by the time there is
    anything to verify, the token has already been sent.

    Which makes pointing a runner at a hostile URL a way to collect somebody's
    GitLab token, and it presents as a failed install."""
    monkeypatch.setenv("TOOL_ARTIFACT_TOKEN", "glpat-secret")
    monkeypatch.setenv("TOOL_ARTIFACT_HOSTS", "gitlab.example")

    ours = _sent("https://gitlab.example/api/v4/projects/7/x.json", monkeypatch)
    theirs = _sent("https://evil.example/x.json", monkeypatch)

    assert ours.get("Private-token") == "glpat-secret"
    assert "Private-token" not in theirs
    assert "glpat-secret" not in str(theirs)


def test_no_configured_host_means_the_credential_is_never_sent(monkeypatch) -> None:
    """Not knowing where it may go is not a reason to send it everywhere. The
    result is a 401 that names the setting, which is diagnosable; the
    alternative is a token on a stranger's server, which is not."""
    monkeypatch.setenv("TOOL_ARTIFACT_TOKEN", "glpat-secret")
    monkeypatch.delenv("TOOL_ARTIFACT_HOSTS", raising=False)

    assert "Private-token" not in _sent("https://gitlab.example/x.json", monkeypatch)


def test_a_refusal_says_the_credential_was_withheld(monkeypatch) -> None:
    """Otherwise "401" on a URL you can open in a browser is unexplainable."""
    import urllib.error
    import urllib.request

    from sandbox_host.tool_resolve import FetchError, _http_get

    monkeypatch.setenv("TOOL_ARTIFACT_TOKEN", "glpat-secret")
    monkeypatch.setenv("TOOL_ARTIFACT_HOSTS", "gitlab.example")

    def urlopen(request):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    _patch_opener(monkeypatch, urlopen)

    with pytest.raises(FetchError, match="TOOL_ARTIFACT_HOSTS"):
        _http_get("https://elsewhere.example/x.json")


def test_the_credential_does_not_follow_a_redirect_to_another_host(monkeypatch) -> None:
    """urllib re-sends custom headers on redirect, across hosts and all. The
    allowlist only ever guarded the FIRST request.

    Not theoretical: GitLab's artifact download 302s to a presigned object-store
    URL whenever `proxy_download` is off, which is an ordinary production
    setting. So the token would arrive at a host nobody put on the list — and
    a hostile URL only has to redirect."""
    import http.server
    import threading

    from sandbox_host.tool_resolve import HOSTS_ENV, TOKEN_ENV, _http_get

    got: list[str | None] = []

    class _Second(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.append(self.headers.get("PRIVATE-TOKEN"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    class _First(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.append(self.headers.get("PRIVATE-TOKEN"))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{second.server_port}/x")
            self.end_headers()

        def log_message(self, *a):
            pass

    second = http.server.HTTPServer(("127.0.0.1", 0), _Second)
    first = http.server.HTTPServer(("localhost", 0), _First)
    for srv in (second, first):
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    monkeypatch.setenv(TOKEN_ENV, "glpat-secret")
    monkeypatch.setenv(HOSTS_ENV, "localhost")
    try:
        _http_get(f"http://localhost:{first.server_port}/m")
    finally:
        first.shutdown()
        second.shutdown()

    assert got[0] == "glpat-secret", "the allowed host is meant to get it"
    assert got[1] is None, "the host it was redirected to is not on the list"


def test_the_fallback_still_asks_whether_the_tool_is_admitted(tmp_path) -> None:
    """Serving the last version that worked must not become a way around the
    only revocation this design has.

    `grant.py` says removing someone from `TRUSTED_KEYS` lapses everything
    they signed. It did not: any HTTPError — 404 and 403 included — reaches
    the fallback, which served the cached bundle without looking at a
    certificate again. An author could trigger it themselves by making the
    project private, and keep running for as long as the cache lived."""
    from sandbox_host import grant as grant_mod
    from sandbox_host.tool_cache import ToolCache
    from sandbox_host.tool_resolve import FetchError, ToolResolver

    data = _bundle()
    cache = ToolCache(tmp_path / "cache", harden=lambda _p: None)
    wire = _Wire(manifest=_manifest(data), bundle=data)
    resolver = ToolResolver(
        cache, builder_id=_BUILDER, arch="x86_64", fetch=wire, state_dir=tmp_path
    )
    assert resolver.resolve("wafer-history", _MANIFEST_URL).sha  # remembered

    # The key that signed it is withdrawn, and the store stops answering.
    import pytest

    monkey = pytest.MonkeyPatch()
    monkey.setattr(grant_mod, "TRUSTED_KEYS", {"someone-else": grant_mod.keypair()[1]})
    try:
        with pytest.raises(FetchError, match="no longer admitted"):
            ToolResolver(
                cache, builder_id=_BUILDER, arch="x86_64", fetch=_Wire(), state_dir=tmp_path
            ).resolve("wafer-history", _MANIFEST_URL)
    finally:
        monkey.undo()

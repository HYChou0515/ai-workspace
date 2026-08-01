"""P9 — checking an artifact BEFORE its URL goes into app.json (#674).

A human runs this with the URL an author just handed them. It answers one
question: would this platform accept it? Everything it checks is something
that would otherwise be discovered after a release, by a user.

It deliberately does not EXECUTE the bundle. The author's build already ran
smoke inside the correct base and refuses to publish without it, and running a
stranger's code on an operator's laptop would be the wrong place to add that.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from workspace_app.tooling.verify import VerifyFailed, verify_artifact

_BUILDER = "registry.example/tool-builder@sha256:beef"
_MANIFEST_URL = "https://gitlab.example/raw/dist/tool.manifest.json?job=build-tool"

_SCHEMA = {"type": "object", "properties": {}}


def _bundle(commands: list[str] | None = None) -> bytes:
    names = ["trend"] if commands is None else commands
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:

        def add(path: str, body: bytes) -> None:
            info = tarfile.TarInfo(path)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))

        add("launch", b"#!/bin/sh\n")
        add("commands.json", json.dumps([{"name": n, "description": "d"} for n in names]).encode())
        for n in names:
            add(
                f"schemas/{n}.json",
                json.dumps({"name": n, "description": "d", "params_json_schema": _SCHEMA}).encode(),
            )
    return buf.getvalue()


def _manifest(data: bytes, **over: object) -> bytes:
    body: dict[str, object] = {
        "format_version": 1,
        "name": "wafer-history",
        "version": "1.4.2",
        "commands": [{"name": "trend", "description": "d", "params_json_schema": _SCHEMA}],
        "builder": _BUILDER,
        "python": "3.12",
        "arch": "x86_64",
        "bundle": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
    }
    body.update(over)
    return json.dumps(body).encode()


def _wire(**pages: bytes):
    def fetch(url: str) -> bytes:
        key = "manifest" if url.split("?")[0].endswith("tool.manifest.json") else "bundle"
        if key not in pages:
            raise OSError(f"{url} is unreachable")
        return pages[key]

    return fetch


def test_a_good_artifact_reports_what_would_be_registered() -> None:
    data = _bundle()
    report = verify_artifact(
        _MANIFEST_URL,
        expected_name="wafer-history",
        builder=_BUILDER,
        arch="x86_64",
        fetch=_wire(manifest=_manifest(data), bundle=data),
    )

    assert report.name == "wafer-history"
    assert report.version == "1.4.2"
    assert report.commands == ("trend",)
    assert report.sha == hashlib.sha256(data).hexdigest()


def test_an_artifact_built_for_another_platform_is_caught_here_not_in_production() -> None:
    data = _bundle()

    with pytest.raises(VerifyFailed) as exc:
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(data, builder="other:1"), bundle=data),
        )

    assert "other:1" in str(exc.value)


def test_a_bundle_whose_contents_disagree_with_its_manifest_is_caught() -> None:
    # The schemas were frozen from one build and the tree came from another:
    # the model would be handed a tool the bundle does not have. Smoke catches
    # this at build time; checking again here costs one comparison and catches
    # anything that reached the artifact store by another route.
    data = _bundle(commands=["drift"])

    with pytest.raises(VerifyFailed) as exc:
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(data), bundle=data),
        )

    assert "drift" in str(exc.value)
    assert "trend" in str(exc.value)


def test_an_unreachable_url_says_so_before_anything_else() -> None:
    with pytest.raises(VerifyFailed, match="unreachable"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(),
        )


def test_the_command_reports_an_accepted_artifact_for_a_human(monkeypatch, capsys) -> None:
    from workspace_app.tooling import verify as verify_mod

    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)
    monkeypatch.setattr(
        verify_mod,
        "verify_artifact",
        lambda url, **kw: verify_mod.VerifyReport("wafer-history", "1.4.2", "a" * 64, ("trend",)),
    )

    assert verify_mod.main([_MANIFEST_URL, "--name", "wafer-history"]) == 0
    out = capsys.readouterr().out
    assert "wafer-history 1.4.2" in out and "trend" in out


def test_the_command_fails_loudly_with_the_reason(monkeypatch, capsys) -> None:
    from workspace_app.tooling import verify as verify_mod

    monkeypatch.setenv("TOOL_BUILDER_ID", _BUILDER)

    def boom(_url, **_kw):
        raise VerifyFailed("built against other:1")

    monkeypatch.setattr(verify_mod, "verify_artifact", boom)

    assert verify_mod.main([_MANIFEST_URL, "--name", "wafer-history"]) == 1
    assert "other:1" in capsys.readouterr().err


def test_the_builder_identity_cannot_be_passed_in_by_a_hurried_operator(
    monkeypatch, capsys
) -> None:
    # It has to be what this deployment runs. A flag would let someone silence
    # a rejection by passing whatever the artifact happens to claim.
    from workspace_app.tooling import verify as verify_mod

    monkeypatch.delenv("TOOL_BUILDER_ID", raising=False)

    assert verify_mod.main([_MANIFEST_URL, "--name", "wafer-history"]) == 2
    assert "TOOL_BUILDER_ID" in capsys.readouterr().err


def test_a_malformed_invocation_shows_usage(capsys) -> None:
    from workspace_app.tooling import verify as verify_mod

    assert verify_mod.main([_MANIFEST_URL]) == 2
    assert "usage" in capsys.readouterr().err.lower()


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


def test_the_operators_fetch_uses_their_own_token(monkeypatch) -> None:
    # The RUNNING app never holds an artifact-store credential; the human
    # running this command already has one, because they were handed the URL.
    import urllib.request

    from workspace_app.tooling.verify import _http_get

    seen: list[urllib.request.Request] = []
    monkeypatch.setenv("TOOL_ARTIFACT_TOKEN", "glpat-operator")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: seen.append(req) or _Response(b"ok")
    )

    assert _http_get("https://gitlab.example/m") == b"ok"
    assert seen[0].get_header("Private-token") == "glpat-operator"


def test_a_public_project_needs_no_token(monkeypatch) -> None:
    import urllib.request

    from workspace_app.tooling.verify import _http_get

    monkeypatch.delenv("TOOL_ARTIFACT_TOKEN", raising=False)
    seen: list[urllib.request.Request] = []
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: seen.append(req) or _Response(b"ok")
    )

    assert _http_get("https://gitlab.example/m") == b"ok"
    assert seen[0].get_header("Private-token") is None


def test_a_url_that_is_not_a_manifest_is_refused_before_any_fetch() -> None:
    with pytest.raises(VerifyFailed, match="tool.manifest.json"):
        verify_artifact(
            "https://gitlab.example/raw/dist/tool.tar.gz",
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(_bundle())),
        )


def test_a_bundle_that_is_not_a_tool_bundle_says_so() -> None:
    import io as _io
    import tarfile as _tarfile

    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = _tarfile.TarInfo("readme.txt")
        info.size = 2
        tar.addfile(info, _io.BytesIO(b"hi"))
    data = buf.getvalue()

    with pytest.raises(VerifyFailed, match="could not be read|not a tool bundle"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(data), bundle=data),
        )


def test_a_bundle_whose_commands_json_is_not_a_file_is_refused() -> None:
    # `extractfile` answers None for a directory (or a device) sitting where a
    # file should be — a broken build, or a bundle built to confuse a reader.
    import io as _io
    import tarfile as _tarfile

    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = _tarfile.TarInfo("commands.json")
        info.type = _tarfile.DIRTYPE
        tar.addfile(info)
    data = buf.getvalue()

    with pytest.raises(VerifyFailed, match="not a tool bundle"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(data), bundle=data),
        )


def test_bytes_that_do_not_match_the_published_sha_are_refused() -> None:
    # The integrity anchor, checked here so a corrupted or swapped artifact is
    # caught by the person registering it rather than by the host at 3am.
    good = _bundle()

    with pytest.raises(VerifyFailed, match="hashes to|bytes"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(good), bundle=_bundle(commands=["trend", "extra"])),
        )


# ─── weight (#674) ───────────────────────────────────────────────────


@pytest.fixture
def signing(monkeypatch):
    """A signing key this deployment trusts, and a limit small enough that a
    test bundle can exceed it. The real limit is pinned in test_grant.py."""
    from workspace_app.tooling import grant as grant_mod

    private, public = grant_mod.keypair()
    monkeypatch.setattr(grant_mod, "TRUSTED_KEYS", {"alice": public})
    monkeypatch.setattr(grant_mod, "DEFAULT_MAX_BYTES", 200)
    return private


def _token(private, *, tool="wafer-history", max_bytes=10_000_000, expires=None):
    from workspace_app.tooling.grant import Grant, issue

    return issue(Grant(tool=tool, max_bytes=max_bytes, expires=expires), private_key=private)


def test_an_oversized_artifact_is_refused(signing) -> None:
    """The gate. The author's CI checks this too, but that runner is theirs —
    this is the check that decides what the platform accepts."""
    data = _bundle()

    with pytest.raises(VerifyFailed, match="limit"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(manifest=_manifest(data), bundle=data),
        )


def test_an_oversized_artifact_is_refused_without_downloading_it(signing) -> None:
    """The size is in the manifest precisely so nobody has to fetch a
    gigabyte to find out it is a gigabyte."""
    data = _bundle()
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        asked.append(url)
        return _manifest(data) if "manifest" in url else data

    with pytest.raises(VerifyFailed):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=fetch,
        )

    assert all("manifest" in url for url in asked), asked


def test_a_certificate_in_the_manifest_raises_the_limit(signing) -> None:
    data = _bundle()

    report = verify_artifact(
        _MANIFEST_URL,
        expected_name="wafer-history",
        builder=_BUILDER,
        arch="x86_64",
        fetch=_wire(manifest=_manifest(data, grant=_token(signing)), bundle=data),
    )

    assert report.name == "wafer-history"


def test_a_certificate_issued_to_another_tool_is_not_accepted_here(signing) -> None:
    """Certificates travel in a public manifest. One that raised any tool's
    limit would be copied the day it was issued."""
    data = _bundle()

    with pytest.raises(VerifyFailed, match="pdf-extract"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(
                manifest=_manifest(data, grant=_token(signing, tool="pdf-extract")), bundle=data
            ),
        )


def test_an_expired_certificate_is_refused_at_the_gate(signing, monkeypatch) -> None:
    """The author built while it was valid and published; the artifact stays
    on the platform long after. The gate reads the certificate as of today."""
    from datetime import date

    from workspace_app.tooling import verify as verify_mod

    data = _bundle()
    monkeypatch.setattr(verify_mod, "_today", lambda: date(2026, 8, 1))

    with pytest.raises(VerifyFailed, match="expired"):
        verify_artifact(
            _MANIFEST_URL,
            expected_name="wafer-history",
            builder=_BUILDER,
            arch="x86_64",
            fetch=_wire(
                manifest=_manifest(data, grant=_token(signing, expires=date(2026, 1, 1))),
                bundle=data,
            ),
        )


def test_an_accepted_oversized_artifact_reports_who_granted_it(signing, capsys) -> None:
    """The question this answers is asked months later: "who reviewed this
    tool, and can tell me why 300MB was reasonable". The operator running
    `verify` is the person holding the artifact at that moment."""
    data = _bundle()

    report = verify_artifact(
        _MANIFEST_URL,
        expected_name="wafer-history",
        builder=_BUILDER,
        arch="x86_64",
        fetch=_wire(manifest=_manifest(data, grant=_token(signing)), bundle=data),
    )

    assert report.granted_by == "alice"


def test_a_tool_inside_the_default_limit_reports_no_grantor(signing, monkeypatch) -> None:
    """Its certificate, if it has one, was never consulted — naming an issuer
    would credit somebody with a decision that did not bear on this."""
    from workspace_app.tooling import grant as grant_mod

    monkeypatch.setattr(grant_mod, "DEFAULT_MAX_BYTES", 10_000)
    data = _bundle()

    report = verify_artifact(
        _MANIFEST_URL,
        expected_name="wafer-history",
        builder=_BUILDER,
        arch="x86_64",
        fetch=_wire(manifest=_manifest(data, grant=_token(signing)), bundle=data),
    )

    assert report.granted_by is None

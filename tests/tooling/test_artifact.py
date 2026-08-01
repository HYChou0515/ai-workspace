"""P1 — the third-party artifact contract (#674).

A tool author's CI emits two files; this module is the pure half that both
the builder and the platform read:

    tool.manifest.json   what the platform gates on, before it trusts a byte
    tool.tar.zst         the bundle those bytes describe

Nothing here does I/O — fetching and extracting live host-side (P4/P6).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workspace_app.tooling.artifact import (
    FORMAT_VERSION,
    BundleRef,
    ChecksumMismatch,
    IncompatibleArtifact,
    ManifestError,
    check_compatible,
    parse_manifest,
    render_manifest,
    verify_bundle,
)

_SHA = "a" * 64


def _manifest_bytes(**over: object) -> bytes:
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
        "builder": "registry.example/tool-builder@sha256:beef",
        "python": "3.12",
        "arch": "x86_64",
        "bundle": {"sha256": _SHA, "size": 1234},
        "source": {"git": "https://gitlab.example/wafer", "sha": "cafe"},
    }
    body.update(over)
    return json.dumps(body).encode()


def test_parse_manifest_reads_what_the_platform_gates_on() -> None:
    m = parse_manifest(_manifest_bytes())

    assert m.name == "wafer-history"
    assert m.version == "1.4.2"
    assert m.builder == "registry.example/tool-builder@sha256:beef"
    assert m.arch == "x86_64"
    assert m.bundle.sha256 == _SHA
    assert m.bundle.size == 1234
    assert [c.name for c in m.commands] == ["trend"]
    assert m.commands[0].params_json_schema == {"type": "object", "properties": {}}


def test_parse_manifest_refuses_a_format_version_it_does_not_know() -> None:
    with pytest.raises(ManifestError) as exc:
        parse_manifest(_manifest_bytes(format_version=99))

    # The author has to learn WHICH side is behind, so both numbers show.
    assert "99" in str(exc.value)
    assert str(FORMAT_VERSION) in str(exc.value)


def test_parse_manifest_turns_malformed_json_into_a_named_refusal() -> None:
    with pytest.raises(ManifestError) as exc:
        parse_manifest(b"{not json")

    assert "not valid json" in str(exc.value).lower()


def test_parse_manifest_names_the_missing_field_instead_of_leaking_keyerror() -> None:
    body = json.loads(_manifest_bytes())
    del body["builder"]

    with pytest.raises(ManifestError) as exc:
        parse_manifest(json.dumps(body).encode())

    assert "builder" in str(exc.value)


_HOST = {"builder": "registry.example/tool-builder@sha256:beef", "arch": "x86_64"}


def test_check_compatible_accepts_an_artifact_built_for_this_platform() -> None:
    m = parse_manifest(_manifest_bytes())

    check_compatible(m, **_HOST)  # does not raise


def test_check_compatible_refuses_a_bundle_built_against_another_base() -> None:
    m = parse_manifest(_manifest_bytes(builder="registry.example/tool-builder@sha256:old"))

    with pytest.raises(IncompatibleArtifact) as exc:
        check_compatible(m, **_HOST)

    # Refusing here is the whole point: the alternative is a segfault at run
    # time, in someone else's tool, with no trace back to the build.
    assert "sha256:old" in str(exc.value)
    assert "sha256:beef" in str(exc.value)


def test_check_compatible_refuses_a_bundle_for_another_architecture() -> None:
    m = parse_manifest(_manifest_bytes(arch="aarch64"))

    with pytest.raises(IncompatibleArtifact) as exc:
        check_compatible(m, **_HOST)

    assert "aarch64" in str(exc.value)


def test_check_compatible_says_nothing_about_which_tool_this_is() -> None:
    """Identity moved to the certificate the platform signs. It used to live
    here, as "the manifest's name must equal the name we registered" — which
    made our name a copy of the author's, so two authors could not both ship
    a `data-fetch`. And a manifest can claim any name for itself, so it was
    never evidence of anything."""
    other = parse_manifest(_manifest_bytes(name="something-else"))

    check_compatible(other, builder="registry.example/tool-builder@sha256:beef", arch="x86_64")


def test_verify_bundle_accepts_the_bytes_the_manifest_describes() -> None:
    data = b"pretend this is a zstd tarball"
    ref = BundleRef(sha256=hashlib.sha256(data).hexdigest(), size=len(data))

    verify_bundle(data, ref)  # does not raise


def test_verify_bundle_refuses_bytes_that_hash_to_something_else() -> None:
    ref = BundleRef(sha256=_SHA, size=len(b"tampered"))

    with pytest.raises(ChecksumMismatch) as exc:
        verify_bundle(b"tampered", ref)

    assert _SHA in str(exc.value)


def test_verify_bundle_calls_out_a_size_that_is_nowhere_near_a_bundle() -> None:
    # The R5 shape: an expired artifact URL answers with a small HTML error
    # page. "hash mismatch" would be true but useless; the size says why.
    ref = BundleRef(sha256=_SHA, size=157_286_400)

    with pytest.raises(ChecksumMismatch) as exc:
        verify_bundle(b"<html>404 Not Found</html>", ref)

    assert "26" in str(exc.value)
    assert "157286400" in str(exc.value)


def test_a_rendered_manifest_parses_back_to_the_same_thing() -> None:
    # The builder writes with `render_manifest`, the platform reads with
    # `parse_manifest`. They are the two ends of one contract, so the
    # round-trip is what keeps a builder-image change from silently emitting
    # something this platform refuses.
    original = parse_manifest(_manifest_bytes())

    assert parse_manifest(render_manifest(original)) == original


def test_a_rendered_manifest_is_readable_json_a_human_can_diff() -> None:
    raw = render_manifest(parse_manifest(_manifest_bytes()))

    assert json.loads(raw)["format_version"] == FORMAT_VERSION
    assert raw.endswith(b"\n")


def test_parse_manifest_refuses_json_that_is_not_an_object() -> None:
    with pytest.raises(ManifestError) as exc:
        parse_manifest(b"[1, 2, 3]")

    assert "json object" in str(exc.value)


def test_source_is_optional_and_survives_the_round_trip_as_absent() -> None:
    # `source` is provenance only, so an author who publishes without it is
    # not doing anything wrong — the platform must not require it.
    body = json.loads(_manifest_bytes())
    del body["source"]

    m = parse_manifest(json.dumps(body).encode())

    assert m.source is None
    assert "source" not in json.loads(render_manifest(m))
    assert parse_manifest(render_manifest(m)) == m


def test_the_host_carries_a_byte_identical_copy_of_this_contract() -> None:
    """sandbox-host is a separate service that deliberately imports nothing
    from workspace_app (see its `protocol.py`), so the artifact contract must
    exist on both sides. Both sides gate on it — the host to decide what to
    unpack, the app to decide what to tell the model a tool accepts — and a
    silent divergence would mean the two disagree about what a manifest says
    while both believe they are right.

    The module is stdlib-only precisely so the copy can be exact, which makes
    "are they the same?" a question a test can answer.
    """
    repo = Path(__file__).resolve().parents[2]
    app = repo / "src" / "workspace_app" / "tooling" / "artifact.py"
    host = repo / "sandbox-host" / "src" / "sandbox_host" / "artifact.py"

    assert host.read_bytes() == app.read_bytes(), (
        "sandbox-host/src/sandbox_host/artifact.py has drifted from the app's "
        "copy — copy it across; the module has no imports beyond the stdlib "
        "exactly so this can stay a verbatim copy"
    )

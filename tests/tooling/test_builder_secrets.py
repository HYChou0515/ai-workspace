"""The builder seals the author's credentials into the bundle
(docs/plan-tool-identity-secrets.md P4).

The author's CI already runs the builder; GitLab hands the job the project's
CI/CD Variables as environment variables. For every name `env.json` marks
`"secret": true`, the builder reads the value from there, seals it to the
platform's public keys (`TOOL_SECRET_RECIPIENTS`, set on the GitLab group), and
puts only the sealed file in the bundle. The value never reaches the manifest,
the bundle in the clear, or the build log.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from tests.tooling.test_builder import _BUILDER, _fake_bundle, _source
from workspace_app.tooling.builder import BUNDLE_NAME, MANIFEST_NAME, BuildError, build_artifact
from workspace_app.tooling.tool_secrets import SECRETS_FILE, open_sealed, public_key_text


def _with_env_json(src: Path, entries: list[dict]) -> Path:
    (src / "env.json").write_text(json.dumps(entries))
    return src


def _members(bundle: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(bundle)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                fh = tar.extractfile(member)
                assert fh is not None
                out[member.name] = fh.read()
    return out


def _build(tmp_path: Path, monkeypatch, env: dict[str, str], entries: list[dict]) -> Path:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    out = tmp_path / "dist"
    build_artifact(
        source=_with_env_json(_source(tmp_path), entries),
        out=out,
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )
    return out


def test_a_secret_is_sealed_into_the_bundle_and_opens_on_the_host(tmp_path, monkeypatch):
    key = X25519PrivateKey.generate()
    out = _build(
        tmp_path,
        monkeypatch,
        {
            "MACHINE_PASSWORD": "hunter2",
            "TOOL_SECRET_RECIPIENTS": public_key_text(key.public_key()),
        },
        [{"name": "MACHINE_PASSWORD", "secret": True}, {"name": "ERP_BASE"}],
    )
    members = _members((out / BUNDLE_NAME).read_bytes())
    assert open_sealed(members[SECRETS_FILE], key) == {"MACHINE_PASSWORD": "hunter2"}


def test_the_value_appears_nowhere_in_the_clear(tmp_path, monkeypatch):
    key = X25519PrivateKey.generate()
    out = _build(
        tmp_path,
        monkeypatch,
        {
            "MACHINE_PASSWORD": "hunter2",
            "TOOL_SECRET_RECIPIENTS": public_key_text(key.public_key()),
        },
        [{"name": "MACHINE_PASSWORD", "secret": True}],
    )
    assert b"hunter2" not in (out / MANIFEST_NAME).read_bytes()
    assert all(
        b"hunter2" not in body for body in _members((out / BUNDLE_NAME).read_bytes()).values()
    )


def test_a_secret_missing_from_ci_fails_the_build_naming_where_to_set_it(tmp_path, monkeypatch):
    key = X25519PrivateKey.generate()
    monkeypatch.delenv("MACHINE_PASSWORD", raising=False)
    with pytest.raises(BuildError, match=r"MACHINE_PASSWORD.*CI/CD.*Variables"):
        _build(
            tmp_path,
            monkeypatch,
            {"TOOL_SECRET_RECIPIENTS": public_key_text(key.public_key())},
            [{"name": "MACHINE_PASSWORD", "secret": True}],
        )
    assert not (tmp_path / "dist" / BUNDLE_NAME).exists(), "a refused build publishes nothing"


def test_no_recipient_fails_the_build(tmp_path, monkeypatch):
    monkeypatch.delenv("TOOL_SECRET_RECIPIENTS", raising=False)
    with pytest.raises(BuildError, match="TOOL_SECRET_RECIPIENTS"):
        _build(
            tmp_path,
            monkeypatch,
            {"MACHINE_PASSWORD": "hunter2"},
            [{"name": "MACHINE_PASSWORD", "secret": True}],
        )


def test_a_tool_with_no_secret_builds_exactly_as_before(tmp_path, monkeypatch):
    """No sealed file, and no need for a recipient: every existing tool's build
    is untouched."""
    monkeypatch.delenv("TOOL_SECRET_RECIPIENTS", raising=False)
    out = _build(tmp_path, monkeypatch, {}, [{"name": "ERP_BASE"}])
    assert SECRETS_FILE not in _members((out / BUNDLE_NAME).read_bytes())


def test_the_manifest_says_which_names_are_secret(tmp_path, monkeypatch):
    key = X25519PrivateKey.generate()
    out = _build(
        tmp_path,
        monkeypatch,
        {
            "MACHINE_PASSWORD": "hunter2",
            "TOOL_SECRET_RECIPIENTS": public_key_text(key.public_key()),
        },
        [{"name": "MACHINE_PASSWORD", "secret": True}],
    )
    env = json.loads((out / MANIFEST_NAME).read_text())["env"]
    assert env == [
        {"name": "MACHINE_PASSWORD", "description": "", "required": None, "secret": True}
    ]

"""Credentials a third-party tool's author keeps out of its source
(docs/plan-tool-identity-secrets.md P4).

The author sets them in GitLab CI/CD Variables and marks the name `"secret":
true` in `env.json`; the builder SEALS each value to the platform's public
key(s) and the sandbox host OPENS them with its private key, handing the values
to that tool's process only. HPKE (RFC 9180) from `cryptography`, which both
sides already depend on — nothing home-made.

`tool_secrets.py` is the same file on both sides (like `artifact.py`), so the
format and the cipher suite cannot drift between the one that seals and the one
that opens.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from workspace_app.tooling.artifact import (
    EnvSpec,
    Manifest,
    parse_env_declaration,
    parse_manifest,
    render_manifest,
)
from workspace_app.tooling.tool_secrets import (
    SecretsError,
    key_id,
    open_sealed,
    parse_recipients,
    private_key_from_text,
    public_key_text,
    seal,
)


def _recipient() -> tuple[X25519PrivateKey, str]:
    key = X25519PrivateKey.generate()
    return key, public_key_text(key.public_key())


def test_a_sealed_value_opens_with_the_deployments_key():
    key, pub = _recipient()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, parse_recipients(pub))
    assert open_sealed(sealed, key) == {"MACHINE_PASSWORD": "hunter2"}


def test_the_sealed_file_holds_no_value_in_the_clear():
    _key, pub = _recipient()
    sealed = seal({"MACHINE_PASSWORD": "hunter2"}, parse_recipients(pub))
    assert b"hunter2" not in sealed
    assert b"MACHINE_PASSWORD" in sealed  # the NAME is not a secret


def test_one_build_opens_on_every_deployment_it_was_sealed_for():
    """Staging and production hold different keys; one artifact serves both."""
    staging, s_pub = _recipient()
    prod, p_pub = _recipient()
    sealed = seal({"PW": "x"}, parse_recipients(f"{s_pub}\n{p_pub}"))
    assert open_sealed(sealed, staging) == {"PW": "x"}
    assert open_sealed(sealed, prod) == {"PW": "x"}


def test_a_deployment_it_was_not_sealed_for_is_told_so():
    _key, pub = _recipient()
    other = X25519PrivateKey.generate()
    sealed = seal({"PW": "x"}, parse_recipients(pub))
    with pytest.raises(SecretsError, match=key_id(other.public_key())):
        open_sealed(sealed, other)


def test_a_tampered_value_does_not_open():
    key, pub = _recipient()
    doc = json.loads(seal({"PW": "x"}, parse_recipients(pub)))
    kid = key_id(key.public_key())
    raw = bytearray(base64.b64decode(doc["recipients"][kid]["PW"]))
    raw[-1] ^= 1
    doc["recipients"][kid]["PW"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(SecretsError, match="PW"):
        open_sealed(json.dumps(doc).encode(), key)


def test_a_value_cannot_be_moved_to_another_name():
    """Each ciphertext is bound to its variable's name, so relabelling one in a
    bundle (say, a token as `PATH`) does not open."""
    key, pub = _recipient()
    doc = json.loads(seal({"PW": "x", "TOKEN": "y"}, parse_recipients(pub)))
    kid = key_id(key.public_key())
    entry = doc["recipients"][kid]
    entry["PW"], entry["TOKEN"] = entry["TOKEN"], entry["PW"]
    with pytest.raises(SecretsError):
        open_sealed(json.dumps(doc).encode(), key)


def test_no_recipient_is_refused_at_seal_time():
    with pytest.raises(SecretsError, match="TOOL_SECRET_RECIPIENTS"):
        seal({"PW": "x"}, [])


@pytest.mark.parametrize("text", ["not-base64!", base64.b64encode(b"short").decode()])
def test_a_malformed_recipient_is_refused(text):
    with pytest.raises(SecretsError, match="TOOL_SECRET_RECIPIENTS"):
        parse_recipients(text)


def test_a_private_key_round_trips_through_its_text_form():
    key = X25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    again = private_key_from_text(base64.b64encode(raw).decode() + "\n")
    assert key_id(again.public_key()) == key_id(key.public_key())


def test_both_copies_of_the_module_are_the_same_file():
    repo = Path(__file__).resolve().parents[2]
    app = repo / "src" / "workspace_app" / "tooling" / "tool_secrets.py"
    host = repo / "sandbox-host" / "src" / "sandbox_host" / "tool_secrets.py"
    assert host.read_bytes() == app.read_bytes(), (
        "sandbox-host/src/sandbox_host/tool_secrets.py has drifted from the app's copy — "
        "copy it across; one side seals and the other opens, so they must agree exactly"
    )


# ── env.json + manifest carry which names are secret ─────────────────────────


def test_env_json_marks_a_name_secret():
    (pw, base) = parse_env_declaration(
        '[{"name": "MACHINE_PASSWORD", "secret": true}, {"name": "ERP_BASE"}]'
    )
    assert pw.secret is True
    assert base.secret is False


def test_secret_must_be_a_boolean():
    with pytest.raises(ValueError, match="`secret` must be true, false or absent"):
        parse_env_declaration('[{"name": "PW", "secret": "yes"}]')


def _manifest(env: tuple[EnvSpec, ...]) -> Manifest:
    from workspace_app.tooling.artifact import BundleRef

    return Manifest(
        format_version=1,
        name="erp",
        version="1.0",
        commands=(),
        builder="b",
        python="3.12",
        arch="x86_64",
        bundle=BundleRef(sha256="a" * 64, size=1),
        source=None,
        grant=None,
        author=None,
        description=None,
        env=env,
    )


def test_the_manifest_carries_the_secret_flag():
    env = (EnvSpec(name="PW", secret=True), EnvSpec(name="BASE"))
    back = parse_manifest(render_manifest(_manifest(env)))
    assert back.env is not None
    assert [e.secret for e in back.env] == [True, False]


def test_a_manifest_without_secrets_renders_as_it_always_did():
    """A key written for every entry would change the bytes of every artifact
    that has none — and with them nothing, except every diff in review."""
    rendered = render_manifest(_manifest((EnvSpec(name="BASE"),)))
    assert b"secret" not in rendered

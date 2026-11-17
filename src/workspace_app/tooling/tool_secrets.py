"""Credentials a third-party tool's author keeps out of its source
(docs/plan-tool-identity-secrets.md P4).

The author sets each value in the tool's GitLab CI/CD Variables and marks its
name `"secret": true` in `env.json`. The builder SEALS the values to every
public key in `TOOL_SECRET_RECIPIENTS` (one per deployment: staging,
production…) and puts the result in the bundle as `SECRETS_FILE`. The sandbox
host OPENS the entry for its own key and hands the values to that tool's
process only — never to the agent or the user. (Every third-party tool in one
item runs as the same tool identity, so this does not defend one tool's
credentials against ANOTHER tool's author; malicious authors are out of scope.)

HPKE (RFC 9180), base mode, X25519 + HKDF-SHA256 + ChaCha20-Poly1305, from
`cryptography` — a published construction, not one of ours. Each ciphertext is
bound to its variable's name through HPKE's `info`, so a value cannot be
relabelled as another name.

This file is copied byte-for-byte between `workspace_app.tooling` (the builder
seals) and `sandbox_host` (the host opens); a test fails when they differ. So:
no imports beyond the stdlib and `cryptography`, which both sides carry.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hpke, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

#: Where the sealed values live inside a bundle, beside `launch`.
SECRETS_FILE = ".tool-secrets.json"
#: The CI variable listing the public keys to seal to — base64 of each raw
#: 32-byte X25519 key, separated by whitespace. Set once, on the GitLab group,
#: by whoever runs the platform; not a secret.
RECIPIENTS_ENV = "TOOL_SECRET_RECIPIENTS"

_FORMAT = 1
_SUITE = hpke.Suite(hpke.KEM.X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.CHACHA20_POLY1305)
_INFO = b"ai-workspace tool secret v1\x00"


class SecretsError(Exception):
    """A value cannot be sealed or opened — the message says which and why."""


def _raw(public: X25519PublicKey) -> bytes:
    return public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(public: X25519PublicKey) -> str:
    """A short, stable name for a public key: what a sealed file is indexed by,
    and what an error tells an operator to look for."""
    return hashlib.sha256(_raw(public)).hexdigest()[:16]


def public_key_text(public: X25519PublicKey) -> str:
    """The form `TOOL_SECRET_RECIPIENTS` takes one of."""
    return base64.b64encode(_raw(public)).decode()


def parse_recipients(text: str) -> list[X25519PublicKey]:
    keys: list[X25519PublicKey] = []
    for word in text.split():
        try:
            keys.append(X25519PublicKey.from_public_bytes(base64.b64decode(word, validate=True)))
        except (binascii.Error, ValueError) as exc:
            raise SecretsError(
                f"{RECIPIENTS_ENV} holds {word!r}, which is not a base64 X25519 public key "
                f"({exc}) — ask the platform team for the current value"
            ) from exc
    return keys


def private_key_from_text(text: str) -> X25519PrivateKey:
    """The host's key file: base64 of the raw 32-byte X25519 private key."""
    try:
        return X25519PrivateKey.from_private_bytes(base64.b64decode(text.strip(), validate=True))
    except (binascii.Error, ValueError) as exc:
        raise SecretsError(
            f"the tool secret key is not a base64 X25519 private key ({exc})"
        ) from exc


def _info(name: str) -> bytes:
    return _INFO + name.encode()


def seal(values: Mapping[str, str], recipients: Iterable[X25519PublicKey]) -> bytes:
    """`{name: value}` sealed to every recipient, as the bytes of `SECRETS_FILE`."""
    keys = list(recipients)
    if not keys:
        raise SecretsError(
            f"env.json marks a variable secret, but {RECIPIENTS_ENV} names no public key "
            "to seal it to — ask the platform team to set it on the GitLab group"
        )
    sealed = {
        key_id(key): {
            name: base64.b64encode(_SUITE.encrypt(value.encode(), key, info=_info(name))).decode()
            for name, value in sorted(values.items())
        }
        for key in keys
    }
    doc = {"format": _FORMAT, "recipients": sealed}
    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()


def open_sealed(data: bytes, private: X25519PrivateKey) -> dict[str, str]:
    """The values sealed for `private`'s public key."""
    try:
        doc = json.loads(data)
        recipients = doc["recipients"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SecretsError(f"{SECRETS_FILE} is not a sealed-secrets file ({exc})") from exc
    if doc.get("format") != _FORMAT:
        raise SecretsError(f"{SECRETS_FILE} has format {doc.get('format')!r}, not {_FORMAT}")
    kid = key_id(private.public_key())
    entry = recipients.get(kid) if isinstance(recipients, dict) else None
    if not isinstance(entry, dict):
        raise SecretsError(
            f"this tool's credentials were not sealed for this deployment (key {kid}); "
            f"add its public key to {RECIPIENTS_ENV} and rebuild the tool"
        )
    values: dict[str, str] = {}
    for name, sealed in entry.items():
        try:
            plain = _SUITE.decrypt(base64.b64decode(sealed), private, info=_info(name))
        except (InvalidTag, binascii.Error, ValueError, TypeError) as exc:
            raise SecretsError(f"the sealed value of {name} does not open ({exc!r})") from exc
        values[name] = plain.decode()
    return values

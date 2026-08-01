"""A raised size limit, signed by the platform (#674).

Every bundle carries its own interpreter and its own wheels, and each one is
downloaded by every host that runs the tool. Left unbounded, tools accumulate
weight nobody chose: a test framework, a plotting stack pulled in for one
chart, a data-science dependency used by one command. So a bundle has a
ceiling.

A ceiling with no way past it is a ceiling that gets deleted the first time a
tool legitimately needs more. The way past it is a certificate: an author asks
the platform team, the platform team reads their tool, and — if the weight is
real — issues one line naming that tool, its new ceiling, and the date the
ceiling lapses.

**Who checks, and what that check is worth.** The author's CI verifies the
certificate so a mistake surfaces in the loop where it can be fixed. That
runner belongs to the author, so the check there is honest rather than
binding — someone determined to lie to themselves can. The gate is
``verify``, which runs here, before a URL is ever registered. Both call this
module, so the rule means one thing in both places.

**A certificate cannot be recalled.** It is verified offline, by the holder,
against a key. Nothing we do afterwards reaches it, which is why the expiry is
the only way one ends and why ``never`` has to be asked for in as many words.

The one lever left is the key list. Each issuer signs with their own, so
taking someone's entry out of ``TRUSTED_KEYS`` lapses everything they ever
signed — attribution and the off switch are the same mechanism.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

#: What a bundle may weigh with no certificate at all, measured on the
#: compressed artifact — the number every host downloads, and the one the
#: manifest already records. It matches the figure the authoring guide has
#: always quoted, so the documented expectation and the enforced rule are the
#: same fact.
DEFAULT_MAX_BYTES = 150 * 1024 * 1024

#: ``{issuer: public key}`` — the signatures this platform accepts, and who
#: each one belongs to. One key per person on the platform team, so a
#: certificate says which of them reviewed the tool WITHOUT the document
#: having to claim it: a name in the payload would be worth the honesty of
#: whoever typed it, and would disagree with the signature the day it
#: mattered.
#:
#: It doubles as the off switch. Somebody leaves, their entry comes out, and
#: every certificate they signed stops verifying — the only revocation this
#: design has other than waiting for an expiry. The cost is that adding or
#: removing an issuer is a code change and a release.
#:
#: Empty until someone runs ``keygen`` — see ``main``. While it is empty every
#: certificate is refused and every tool gets ``DEFAULT_MAX_BYTES``.
TRUSTED_KEYS: dict[str, str] = {}

#: The file an author drops a certificate into, at the root of their tool's
#: source. A committed file rather than a CI variable because it is not a
#: secret — it is a public statement about one tool that anyone may read and
#: nobody but us can write.
GRANT_FILE = "tool-size-grant.token"

_NEVER = "never"


class GrantError(Exception):
    """This certificate cannot be used, and the message says why.

    Always raised rather than quietly falling back to the default: an author
    whose grant lapsed would otherwise be told their bundle is too big, and
    would go and delete dependencies to fix a paperwork problem."""


@dataclass(frozen=True)
class Grant:
    """A ceiling, for one tool, until one date."""

    tool: str
    max_bytes: int
    #: ``None`` is ``never``. Spelled out at issue time rather than being what
    #: you get by leaving the field off.
    expires: date | None
    #: Who issued it. Filled in by `verify`, from the key that signed —
    #: `issue` neither takes nor writes it, because it is not something a
    #: document can claim about itself. Empty on the way in.
    issued_by: str = ""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def keypair() -> tuple[bytes, str]:
    """A new signing key: the private half as PEM, the public half as one
    base64 line to paste into ``TRUSTED_KEYS``.

    The private half is returned rather than stored — where it belongs is a
    decision for whoever holds it, and this module has no business having an
    opinion about that."""
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return pem, base64.b64encode(public).decode()


def issue(grant: Grant, *, private_key: bytes) -> str:
    """Sign a certificate. One line of ASCII, because it is delivered by
    whatever the author used to ask — usually mail."""
    key = serialization.load_pem_private_key(private_key, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise GrantError(f"the signing key must be ed25519, got {type(key).__name__}")
    payload = json.dumps(
        {
            "tool": grant.tool,
            "max_bytes": grant.max_bytes,
            "expires": grant.expires.isoformat() if grant.expires else _NEVER,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"{_b64(payload)}.{_b64(key.sign(payload))}"


def _signed_payload(token: str, public_keys: dict[str, str]) -> tuple[bytes, str]:
    """The payload bytes and the issuer, once their key has vouched."""
    if not public_keys:
        raise GrantError(
            "no trusted signing key is configured, so no certificate can be checked — "
            "the platform team has not run `keygen` yet"
        )
    head, sep, tail = token.partition(".")
    if not sep or "." in tail:
        raise GrantError("a certificate is two dot-separated parts; this is not one")
    try:
        payload, signature = _unb64(head), _unb64(tail)
    except ValueError as exc:
        # `binascii.Error`, which is what bad base64 actually raises, is a
        # ValueError — so this covers it without reaching into a module
        # `base64` happens to import.
        raise GrantError(f"the certificate is damaged: {exc}") from exc

    for issuer, candidate in public_keys.items():
        try:
            public = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(candidate))
            public.verify(signature, payload)
            return payload, issuer
        except (InvalidSignature, ValueError):
            continue
    raise GrantError(
        "the signature does not match any key this platform trusts — the certificate "
        "was altered after it was issued, or was not issued by us"
    )


def verify(token: str, *, public_keys: dict[str, str], tool: str, today: date) -> Grant:
    """Read a certificate, or raise ``GrantError`` saying why it cannot be
    used for this tool today."""
    payload, issuer = _signed_payload(token, public_keys)
    try:
        body = json.loads(payload)
        granted = Grant(
            tool=body["tool"],
            max_bytes=int(body["max_bytes"]),
            expires=None if body["expires"] == _NEVER else date.fromisoformat(body["expires"]),
            issued_by=issuer,
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise GrantError(f"the certificate is malformed at {exc}") from exc

    # Named tools only. A certificate is not a secret and travels in a
    # manifest anyone can read, so an unnamed one would raise the ceiling for
    # every author who saw it.
    if granted.tool != tool:
        raise GrantError(
            f"this certificate was issued for {granted.tool!r}, and this build is {tool!r}"
        )
    if granted.expires is not None and today > granted.expires:
        raise GrantError(
            f"the certificate for {granted.tool!r} expired on {granted.expires.isoformat()} — "
            "ask the platform team to review the tool again"
        )
    return granted


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MB"


def check_size(
    *,
    tool: str,
    size: int,
    token: str | None,
    public_keys: dict[str, str] | None = None,
    today: date | None = None,
) -> str | None:
    """``None`` if a bundle this size is allowed, else a sentence saying why
    not. The one place the rule lives: the author's build and the platform's
    gate both call this, so neither can drift into being stricter.

    **A certificate only matters above the default.** Below it there is
    nothing to raise, so a lapsed or unreadable certificate is not consulted
    and cannot fail a build — a tool that has since slimmed down, or whose
    grant ran out while it no longer needed one, publishes normally."""
    if size <= DEFAULT_MAX_BYTES:
        return None
    if token is None:
        return (
            f"this bundle is {_mb(size)}, and a tool with no certificate may "
            f"weigh {_mb(DEFAULT_MAX_BYTES)}"
        )
    try:
        granted = verify(
            token,
            public_keys=TRUSTED_KEYS if public_keys is None else public_keys,
            tool=tool,
            today=today or date.today(),
        )
    except GrantError as exc:
        return f"this bundle is {_mb(size)} and its certificate cannot be used: {exc}"
    if size > granted.max_bytes:
        return (
            f"this bundle is {_mb(size)}, and the certificate for {tool!r} "
            f"allows {_mb(granted.max_bytes)}"
        )
    return None


# ─── the command the platform team runs ──────────────────────────────

_USAGE = """usage:
  python -m workspace_app.tooling.grant keygen --key <path> --as <handle>
  python -m workspace_app.tooling.grant issue --tool <name> --max-mb <n> \
--expires <YYYY-MM-DD|never> --key <path>"""


def _flags(argv: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    rest = iter(argv)
    for flag in rest:
        value = next(rest, None)
        if not flag.startswith("--") or value is None:
            raise GrantError(f"expected `--flag value` pairs, got {flag!r}")
        parsed[flag] = value
    return parsed


def _keygen(flags: dict[str, str], out, err) -> int:
    import os
    from pathlib import Path

    if "--as" not in flags:
        print(
            "--as is required: the handle this key is trusted under. A key with "
            'nobody\'s name on it cannot answer "who approved this", which is the '
            "only reason certificates are signed per person.",
            file=err,
        )
        return 2
    who = flags["--as"]
    path = Path(flags["--key"])
    try:
        # O_EXCL rather than a prior `exists()`: overwriting is not a lost
        # file, it silently invalidates every certificate ever issued.
        # 0600 at creation, so the key is never briefly world-readable.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(
            f"{path} exists — writing a new key here would invalidate every "
            "certificate signed with the old one. Move it aside first.",
            file=err,
        )
        return 2
    private, public = keypair()
    with os.fdopen(handle, "wb") as fh:
        fh.write(private)
    print(f"wrote {path} — this is {who}'s signing key. It stays on this machine.", file=out)
    # Spelled out, not derived from `__name__`: run the documented way
    # (`python -m …`) that is `__main__`, and the instruction would name a
    # file nobody can find.
    print("Add this line to TRUSTED_KEYS in src/workspace_app/tooling/grant.py:", file=out)
    print(f'    "{who}": "{public}",', file=out)
    print(
        "It takes effect on the next release. Taking it out later lapses every "
        f"certificate {who} has signed.",
        file=out,
    )
    return 0


def _issue(flags: dict[str, str], out, err) -> int:
    from pathlib import Path

    if "--expires" not in flags:
        print(
            "--expires is required: pass a date (2026-09-01) or `never`. A "
            "certificate that never lapses is a decision, not a default.",
            file=err,
        )
        return 2
    raw = flags["--expires"]
    expires = None if raw == _NEVER else date.fromisoformat(raw)
    path = Path(flags["--key"])
    try:
        private = path.read_bytes()
    except OSError as exc:
        print(f"cannot read the signing key at {path}: {exc}", file=err)
        return 2
    grant = Grant(
        tool=flags["--tool"],
        max_bytes=int(flags["--max-mb"]) * 1024 * 1024,
        expires=expires,
    )
    # The certificate alone on stdout: it is about to be copied into a reply.
    print(issue(grant, private_key=private), file=out)
    return 0


def main(argv: list[str]) -> int:
    """Make a signing key, or sign a certificate for a tool that was
    reviewed. Both are deliberate, occasional, human acts."""
    import sys

    out, err = sys.stdout, sys.stderr
    if not argv or argv[0] not in {"keygen", "issue"}:
        print(_USAGE, file=err)
        return 2
    try:
        flags = _flags(argv[1:])
        return (_keygen if argv[0] == "keygen" else _issue)(flags, out, err)
    except (GrantError, KeyError, ValueError) as exc:
        print(f"{exc}\n\n{_USAGE}", file=err)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised via `main`
    import sys

    raise SystemExit(main(sys.argv[1:]))

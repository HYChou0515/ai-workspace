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
import csv
import json
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote, urlsplit

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

#: The reviewable record of which names have been handed out. Read when
#: issuing, written by hand in the same change that registers the tool — the
#: same shape as adding a key, and for the same reason: both are decisions
#: worth seeing in a diff.
#:
#: It exists because a certificate binds a NAME, and certificates are public:
#: they travel in manifests anyone can read. Two certificates for one name are
#: two tools that can each pass as the other, and the second author can lift
#: the first one's certificate straight out of a published manifest. Nothing
#: else can catch it, because a name is not registered anywhere until after it
#: has been issued.
REGISTRY_FILE = "tool-registry.csv"

#: What a manifest URL ends in — a `--source` ending here is one artifact,
#: not the place they live.
MANIFEST_BASENAME = "tool.manifest.json"


class GrantError(Exception):
    """This certificate cannot be used, and the message says why.

    Always raised rather than quietly falling back to the default: an author
    whose grant lapsed would otherwise be told their bundle is too big, and
    would go and delete dependencies to fix a paperwork problem."""


@dataclass(frozen=True)
class Grant:
    """One tool's admission to this platform, and what it may weigh."""

    #: OUR name for the tool — the key in an app's `external_tools`. Identity
    #: comes from the certificate rather than from what the author called
    #: their command, so two authors may both ship a `data-fetch`.
    tool: str
    #: Where this tool's artifacts live — a URL PREFIX, not one artifact.
    #:
    #: Certificates are public: they ride in manifests anyone can read. Bound
    #: to a name alone, one could be lifted out of a published manifest and
    #: used to be admitted under that name from somewhere else entirely.
    #:
    #: A prefix rather than the manifest URL because rolling back means
    #: pointing at one job's artifact instead of the ref's latest — a
    #: different URL under the same project. Binding to the whole URL would
    #: refuse exactly the move an operator makes when something is wrong.
    source: str
    max_bytes: int
    #: A PUBLISHING deadline on `max_bytes` — "you are in a hurry, ship it,
    #: but be under the limit within a month". ``None`` when nothing was
    #: raised, or when the weight is permanent.
    #:
    #: Named for what running out of it does. It stops the author publishing
    #: another oversized build; it does not stop the tool. What is already
    #: running is untouched, because the delay is theirs and the people using
    #: the tool did not agree to it.
    publish_until: date | None = None
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
            "source": grant.source,
            "max_bytes": grant.max_bytes,
            "publish_until": (grant.publish_until.isoformat() if grant.publish_until else _NEVER),
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


def verify(token: str, *, public_keys: dict[str, str], tool: str) -> Grant:
    """Read a certificate, or raise ``GrantError`` saying why it is not a
    usable one for this tool.

    Says nothing about the expiry, which bounds only the size allowance —
    `check_size` is what reads it. A tool does not stop being itself, or stop
    being admitted, because a deadline for slimming down went by."""
    payload, issuer = _signed_payload(token, public_keys)
    try:
        body = json.loads(payload)
        granted = Grant(
            tool=body["tool"],
            source=body["source"],
            max_bytes=int(body["max_bytes"]),
            publish_until=(
                None
                if body["publish_until"] == _NEVER
                else date.fromisoformat(body["publish_until"])
            ),
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
    return granted


def admit(
    *,
    tool: str,
    url: str,
    token: str | None,
    public_keys: dict[str, str] | None = None,
) -> str | None:
    """``None`` if this artifact may run here as `tool`, else why not.

    The gate every third-party tool passes, applied wherever one is about to
    run: pasting a URL into an app is not the approval — a certificate the
    platform signed is. Deliberately says nothing about weight, which ends on
    its own schedule and must not stop a running tool."""
    if token is None:
        return (
            f"{tool!r} carries no certificate. Every third-party tool needs one "
            "issued by the platform team; ask them to review it."
        )
    try:
        granted = verify(
            token,
            public_keys=TRUSTED_KEYS if public_keys is None else public_keys,
            tool=tool,
        )
    except GrantError as exc:
        return str(exc)
    # Compared with percent-encoding undone on both sides. GitLab's API form
    # writes the project as `rca%2Fwafer-history` and its web form as
    # `rca/wafer-history`; the same place written two ways has to match.
    if not unquote(url).startswith(unquote(granted.source)):
        return (
            f"the certificate for {tool!r} is good for artifacts under "
            f"{granted.source}, and this one came from {url}"
        )
    return None


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
    """``None`` if a bundle this size may be PUBLISHED, else why not.

    Weight only — whether the tool is admitted at all is `admit`'s question,
    and mixing them would let a paperwork problem read as "too big".

    Applied where publishing happens: the author's build and the registration
    gate. Never by the host, on purpose. A raised allowance comes with a
    deadline — "ship it now, be under the limit within a month" — and when
    that lapses the author cannot publish another oversized build, while what
    is already running stays exactly where it is. Their delay is theirs; the
    people using the tool did not agree to it."""
    when = today or date.today()
    allowed = DEFAULT_MAX_BYTES
    lapsed: date | None = None

    if token is not None:
        try:
            granted = verify(
                token,
                public_keys=TRUSTED_KEYS if public_keys is None else public_keys,
                tool=tool,
            )
        except GrantError:
            # Unusable for some other reason — `admit` is what reports that.
            # Here it simply raises nothing, and the default applies.
            granted = None
        if granted is not None and granted.max_bytes > allowed:
            if granted.publish_until is None or when <= granted.publish_until:
                allowed = granted.max_bytes
            else:
                lapsed = granted.publish_until

    if size <= allowed:
        return None
    if lapsed is not None:
        return (
            f"this bundle is {_mb(size)}. The larger allowance for {tool!r} was to "
            f"publish until {lapsed.isoformat()}, so {_mb(allowed)} applies again"
        )
    return f"this bundle is {_mb(size)}, and {tool!r} may weigh {_mb(allowed)}"


# ─── the command the platform team runs ──────────────────────────────

_USAGE = """usage:
  python -m workspace_app.tooling.grant keygen --key <path> --as <handle>
  python -m workspace_app.tooling.grant issue --tool <name> --source <url prefix> --key <path>
      [--registry <path to tool-registry.csv>]
      [--max-mb <n> --publish-until <YYYY-MM-DD|never>]  raise the size limit,
                                                        with a deadline to fix it"""


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

    # `--max-mb` without a deadline is the shape this exists to prevent. A
    # raised allowance is meant to be temporary — "ship it now, be under the
    # limit within a month" — and one granted forever is just a bigger limit
    # for one tool that nobody ever revisits.
    if "--max-mb" in flags and "--publish-until" not in flags:
        print(
            "--publish-until is required with --max-mb: a raised size allowance is a "
            "deadline for the author to get back under the limit. Pass `never` if "
            "the weight is permanent and you have decided that.",
            file=err,
        )
        return 2

    if "--source" not in flags:
        print(
            "--source is required: the URL prefix this tool's artifacts live under, "
            "e.g. https://gitlab.example/api/v4/projects/rca%2Fwafer-history/ . A "
            "certificate is public, so without it one can be copied out of a "
            "published manifest and used from somewhere else.",
            file=err,
        )
        return 2
    source = flags["--source"]
    if MANIFEST_BASENAME in source:
        print(
            f"--source is a prefix, not one artifact: {source} names a single "
            "manifest, so rolling back to an older build — which is a different "
            "URL under the same project — would be refused. Cut it back to the "
            "project.",
            file=err,
        )
        return 2
    if urlsplit(source).path.strip("/") == "":
        print(
            f"--source names a server, not a place on it: {source} would admit "
            "anything published anywhere on that host under this name. Include "
            "the project path.",
            file=err,
        )
        return 2

    publish_until = _a_date(flags.get("--publish-until"))
    tool = flags["--tool"]

    registry = Path(flags.get("--registry", REGISTRY_FILE))
    try:
        rows = list(csv.DictReader(registry.read_text("utf-8").splitlines()))
    except OSError as exc:
        # Not "no name is taken" — the state where nothing is being checked.
        print(
            f"cannot read the issued-name registry at {registry}: {exc}. "
            "Run this from the repository, or pass --registry.",
            file=err,
        )
        return 2
    taken = next((r for r in rows if r["tool"] == tool), None)
    if taken is not None:
        print(
            f"{tool!r} is already issued to {taken['source']} "
            f"(by {taken['issued_by']}, {taken['issued_on']}). Two certificates for one "
            "name admit each other's tools — pick a different name.",
            file=err,
        )
        return 2

    path = Path(flags["--key"])
    try:
        private = path.read_bytes()
    except OSError as exc:
        print(f"cannot read the signing key at {path}: {exc}", file=err)
        return 2

    # No `--max-mb` means the ordinary limit, which is what almost every tool
    # gets: admitting a tool and raising its ceiling are different decisions,
    # and only one of them is routine.
    megabytes = int(flags["--max-mb"]) if "--max-mb" in flags else DEFAULT_MAX_BYTES // 1024 // 1024
    grant = Grant(
        tool=tool,
        source=source,
        max_bytes=megabytes * 1024 * 1024,
        publish_until=publish_until,
    )
    # The certificate alone on stdout: it is about to be copied into a reply.
    print(issue(grant, private_key=private), file=out)
    # The row to record, on stderr so it does not travel with the certificate.
    print(
        f"\nAdd to {registry.name}:\n  {tool},<their repo>,<you>,<today>",
        file=err,
    )
    return 0


def _a_date(raw: str | None) -> date | None:
    """A date, or None for `never` and for "not given"."""
    return None if raw is None or raw == _NEVER else date.fromisoformat(raw)


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

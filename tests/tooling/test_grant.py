"""A raised size limit, signed by the platform (#674).

Bundles arrive with weight nobody meant to ship. The limit is the answer to
that, and a limit with no way out is a limit that gets deleted the first time
a tool legitimately needs more — so the platform team can review a tool and
issue a certificate naming it, its ceiling, and when that ceiling lapses.

The certificate is verified in two places that trust each other differently.
The author's CI checks it to find out early; that runner is theirs, so the
check there is honest rather than binding. Ours is the gate. Both run this
module, so the rule has one meaning.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from workspace_app.tooling.grant import (
    DEFAULT_MAX_BYTES,
    Grant,
    GrantError,
    check_size,
    issue,
    keypair,
    verify,
)


@pytest.fixture
def keys() -> tuple[bytes, dict[str, str]]:
    """One issuer's throwaway keypair: their private key, and the trusted-key
    entry it is published as — keyed by WHO, because the key is what says so."""
    private, public = keypair()
    return private, {"alice": public}


def test_a_certificate_says_which_tool_how_big_and_until_when(keys):
    private, public = keys
    token = issue(
        Grant(tool="pdf-extract", max_bytes=300 * 1024 * 1024, publish_until=date(2026, 9, 1)),
        private_key=private,
    )

    granted = verify(token, public_keys=public, tool="pdf-extract")

    assert granted.tool == "pdf-extract"
    assert granted.max_bytes == 300 * 1024 * 1024
    assert granted.publish_until == date(2026, 9, 1)


def test_a_certificate_fits_on_one_line_so_it_can_be_pasted_into_a_reply(keys):
    """Issuing happens out of band: an author asks, the platform team reads
    their tool, and answers. Whatever comes out of that has to survive being
    pasted into mail, so it is one line of ASCII with no wrapping."""
    private, _ = keys

    token = issue(Grant(tool="t", max_bytes=1, publish_until=None), private_key=private)

    assert "\n" not in token
    assert token.isascii()


def test_a_certificate_for_another_tool_is_refused(keys):
    """Otherwise one raised limit is every author's raised limit: the token is
    not a secret, and it travels in a manifest anyone can read."""
    private, public = keys
    token = issue(Grant(tool="pdf-extract", max_bytes=10, publish_until=None), private_key=private)

    with pytest.raises(GrantError, match="pdf-extract"):
        verify(token, public_keys=public, tool="wafer-history")


def test_a_lapsed_deadline_does_not_stop_the_certificate_being_read(keys):
    """The deadline bounds PUBLISHING, not the certificate and not the tool.
    Reading one after its deadline has to still say which tool it is, or a
    late author would stop being admitted rather than stop shipping."""
    private, public = keys
    token = issue(
        Grant(tool="t", max_bytes=10, publish_until=date(2026, 9, 1)), private_key=private
    )

    granted = verify(token, public_keys=public, tool="t")

    assert granted.tool == "t"
    assert granted.publish_until == date(2026, 9, 1)


def test_the_allowance_lasts_the_whole_of_its_last_day(keys):
    # A deadline the holder cannot use for its whole final day is a date that
    # means something other than what it says.
    private, public = keys
    token = issue(
        Grant(tool="t", max_bytes=300 * 1024 * 1024, publish_until=date(2026, 9, 1)),
        private_key=private,
    )

    assert (
        check_size(
            tool="t",
            size=300 * 1024 * 1024,
            token=token,
            public_keys=public,
            today=date(2026, 9, 1),
        )
        is None
    )


def test_a_certificate_can_carry_no_deadline_at_all(keys):
    """Some tools are permanently large and re-issuing every year is a chore
    that gets automated into meaninglessness. `never` is spelled out rather
    than being what you get by leaving the field off."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)

    granted = verify(token, public_keys=public, tool="t")

    assert granted.publish_until is None


def test_raising_the_number_after_it_was_signed_is_refused(keys):
    """The whole point of signing. Someone edits the ceiling in a token they
    were given; the signature stops covering what the payload now says."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)
    payload, _, signature = token.partition(".")
    import base64

    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["max_bytes"] = 99_000_000_000
    forged = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")

    with pytest.raises(GrantError, match="signature"):
        verify(f"{forged}.{signature}", public_keys=public, tool="t")


def test_a_certificate_signed_by_someone_else_is_refused(keys):
    private, _ = keys
    other_public = {"mallory": keypair()[1]}
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)

    with pytest.raises(GrantError, match="signature"):
        verify(token, public_keys=other_public, tool="t")


def test_any_of_the_trusted_keys_may_have_signed_it(keys):
    """Rotation: a new key starts signing while certificates from the old one
    are still in the field. Neither the author nor the platform team sees this
    happen."""
    private, public = keys
    retired = {"bob": keypair()[1]}
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)

    assert verify(token, public_keys={**retired, **public}, tool="t")


def test_a_certificate_is_refused_when_no_key_is_trusted_yet(keys):
    """The state this ships in, until someone runs keygen. It must read as
    "we have not set this up" rather than as the author's mistake."""
    private, _ = keys
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)

    with pytest.raises(GrantError, match="no trusted"):
        verify(token, public_keys={}, tool="t")


@pytest.mark.parametrize("bad", ["", "not-a-token", "a.b.c", "!!!.???"])
def test_a_damaged_certificate_says_so_instead_of_crashing(bad, keys):
    _, public = keys

    with pytest.raises(GrantError):
        verify(bad, public_keys=public, tool="t")


# ─── the rule both sides apply ───────────────────────────────────────


def test_a_bundle_within_the_default_needs_no_certificate(keys):
    _, public = keys

    assert check_size(tool="t", size=DEFAULT_MAX_BYTES, token=None, public_keys=public) is None


def test_a_bundle_over_the_default_with_no_certificate_is_refused(keys):
    _, public = keys

    reason = check_size(tool="t", size=DEFAULT_MAX_BYTES + 1, token=None, public_keys=public)

    assert reason is not None and "150.0MB" in reason


def test_a_certificate_lets_a_bundle_weigh_what_it_grants(keys):
    private, public = keys
    token = issue(
        Grant(tool="t", max_bytes=300 * 1024 * 1024, publish_until=None), private_key=private
    )

    assert (
        check_size(
            tool="t",
            size=300 * 1024 * 1024,
            token=token,
            public_keys=public,
        )
        is None
    )


def test_a_bundle_over_even_its_certificate_is_refused(keys):
    private, public = keys
    token = issue(
        Grant(tool="t", max_bytes=300 * 1024 * 1024, publish_until=None), private_key=private
    )

    reason = check_size(
        tool="t",
        size=400 * 1024 * 1024,
        token=token,
        public_keys=public,
    )

    assert reason is not None and "300.0MB" in reason


def test_a_lapsed_certificate_does_not_fail_a_tool_that_no_longer_needs_one(keys):
    """Found by building for real: a 41MB bundle carrying a certificate the
    platform could not read failed its build, for a reason that had nothing to
    do with its weight.

    Below the default there is nothing to raise, so the certificate is not
    consulted. A tool that slimmed down, or whose grant ran out while it no
    longer needed one, publishes normally."""
    private, public = keys
    stale = issue(
        Grant(tool="t", max_bytes=10, publish_until=date(2020, 1, 1)), private_key=private
    )

    assert (
        check_size(
            tool="t",
            size=40 * 1024 * 1024,
            token=stale,
            public_keys=public,
        )
        is None
    )


def test_an_expired_certificate_is_reported_when_the_weight_needs_it(keys):
    """Above the default the certificate is load-bearing again, and the reason
    has to name the expiry — telling this author to delete dependencies would
    send them to fix the wrong thing."""
    private, public = keys
    stale = issue(
        Grant(tool="t", max_bytes=300 * 1024 * 1024, publish_until=date(2026, 1, 1)),
        private_key=private,
    )

    reason = check_size(
        tool="t",
        size=200 * 1024 * 1024,
        token=stale,
        public_keys=public,
    )

    assert reason is not None and "2026-01-01" in reason


def test_the_default_limit_is_the_number_the_authoring_guide_quotes():
    """The guide tells authors each dependency ships inside a ~150MB artifact.
    If the enforced number were a different one, the sentence they plan
    against would be a lie."""
    assert DEFAULT_MAX_BYTES == 150 * 1024 * 1024


# ─── the command the platform team runs (#674) ───────────────────────


def _run(argv, capsys) -> tuple[int, str, str]:
    from workspace_app.tooling.grant import main

    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_keygen_then_issue_produces_a_certificate_the_platform_accepts(tmp_path, capsys):
    """The whole operator path, end to end: make a key, print the line that
    goes into the source, sign a certificate with it, and have that
    certificate check out against that line."""
    key = tmp_path / "tool-grant.pem"

    code, printed, _ = _run(["keygen", "--key", str(key), "--as", "alice"], capsys)
    assert code == 0
    line = next(ln.strip() for ln in printed.splitlines() if ln.strip().startswith('"alice"'))
    public = {"alice": line.split('"')[3]}

    code, token, _ = _run(
        ["issue", "--tool", "pdf-extract", "--max-mb", "300", "--publish-until", "2026-09-01"]
        + ["--key", str(key)],
        capsys,
    )

    assert code == 0
    granted = verify(token.strip(), public_keys=public, tool="pdf-extract")
    assert granted.max_bytes == 300 * 1024 * 1024
    assert granted.publish_until == date(2026, 9, 1)


def test_the_private_key_is_written_readable_only_by_its_owner(tmp_path, capsys):
    key = tmp_path / "tool-grant.pem"

    _run(["keygen", "--key", str(key), "--as", "alice"], capsys)

    assert key.stat().st_mode & 0o077 == 0, oct(key.stat().st_mode)


def test_keygen_refuses_to_overwrite_an_existing_key(tmp_path, capsys):
    """Overwriting is not a lost file — it silently invalidates every
    certificate ever issued, and the tools holding them fail at their next
    build with a signature error nobody will connect to this."""
    key = tmp_path / "tool-grant.pem"
    _run(["keygen", "--key", str(key), "--as", "alice"], capsys)
    before = key.read_bytes()

    code, _, err = _run(["keygen", "--key", str(key), "--as", "alice"], capsys)

    assert code == 2
    assert "exists" in err
    assert key.read_bytes() == before


def test_a_permanent_allowance_has_to_be_asked_for_in_those_words(tmp_path, capsys):
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key), "--as", "alice"], capsys)

    code, token, _ = _run(
        ["issue", "--tool", "t", "--max-mb", "300", "--publish-until", "never", "--key", str(key)],
        capsys,
    )

    assert code == 0
    payload = json.loads(
        __import__("base64").urlsafe_b64decode(
            token.split(".")[0] + "=" * (-len(token.split(".")[0]) % 4)
        )
    )
    assert payload["publish_until"] == "never"


def test_raising_the_limit_without_a_deadline_is_refused(tmp_path, capsys):
    """A raised allowance is meant to be temporary — "ship it now, be under the
    limit within a month". One granted with no deadline is just a bigger limit
    for one tool that nobody revisits, so it has to be said out loud."""
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key), "--as", "alice"], capsys)

    code, _, err = _run(["issue", "--tool", "t", "--max-mb", "300", "--key", str(key)], capsys)

    assert code == 2
    assert "--publish-until" in err


def test_a_certificate_is_printed_alone_so_it_can_be_copied(tmp_path, capsys):
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key), "--as", "alice"], capsys)

    _, printed, _ = _run(
        ["issue", "--tool", "t", "--max-mb", "1", "--publish-until", "never", "--key", str(key)],
        capsys,
    )

    assert len(printed.strip().splitlines()) == 1


def test_an_unreadable_key_is_reported_rather_than_traced(tmp_path, capsys):
    code, _, err = _run(
        ["issue", "--tool", "t", "--max-mb", "1", "--publish-until", "never"]
        + ["--key", str(tmp_path / "nope.pem")],
        capsys,
    )

    assert code == 2
    assert "nope.pem" in err


def test_usage_is_printed_for_an_unknown_subcommand(capsys):
    code, _, err = _run(["sign-everything"], capsys)

    assert code == 2
    assert "keygen" in err and "issue" in err


def test_keygen_names_the_real_file_when_run_the_documented_way(tmp_path):
    """The instruction it prints has to name a file that exists.

    Run as `python -m workspace_app.tooling.grant` — the way the docs say —
    `__name__` is `__main__`, so a filename derived from it sent the operator
    to `__main__.py`. Calling `main()` in-process cannot catch this: under
    pytest the module has its real name. Only the documented invocation
    reproduces it."""
    import subprocess
    import sys

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "workspace_app.tooling.grant",
            "keygen",
            "--key",
            str(tmp_path / "k"),
            "--as",
            "alice",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "workspace_app/tooling/grant.py" in done.stdout, done.stdout


# ─── who issued it ───────────────────────────────────────────────────


def test_a_certificate_says_which_of_us_issued_it(keys):
    """Not for defending against a forged name — everyone who can sign is on
    the platform team already. It is so that "who reviewed this tool, and can
    tell me why 400MB was reasonable" has an answer six months later."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)

    assert verify(token, public_keys=public, tool="t").issued_by == "alice"


def test_two_issuers_are_told_apart_by_their_signatures(keys):
    alice_private, trusted = keys
    bob_private, bob_public = keypair()
    both = {**trusted, "bob": bob_public}

    from_alice = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=alice_private)
    from_bob = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=bob_private)

    assert verify(from_alice, public_keys=both, tool="t").issued_by == ("alice")
    assert verify(from_bob, public_keys=both, tool="t").issued_by == "bob"


def test_dropping_someones_key_lapses_what_they_issued(keys):
    """The reason attribution is worth doing this way rather than as a field
    someone types: it is also the off switch. Somebody leaves, their key comes
    out of the list, and every certificate they signed stops verifying — which
    is the only revocation this design has other than waiting for an expiry."""
    private, _ = keys
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)
    theirs_removed = {"bob": keypair()[1]}

    with pytest.raises(GrantError, match="signature"):
        verify(token, public_keys=theirs_removed, tool="t")


def test_the_payload_cannot_claim_an_issuer(keys):
    """The name comes from the key that signed, never from the document. A
    field saying who wrote it would be worth exactly as much as the honesty of
    whoever wrote it, and would disagree with the signature the day it
    mattered."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, publish_until=None), private_key=private)
    payload = json.loads(
        __import__("base64").urlsafe_b64decode(
            token.split(".")[0] + "=" * (-len(token.split(".")[0]) % 4)
        )
    )

    assert "issued_by" not in payload


def test_a_key_with_nobody_s_name_on_it_is_refused(tmp_path, capsys):
    """The handle is what makes a certificate answerable months later. A key
    generated without one would sign certificates that verify perfectly and
    credit nobody — which is the whole thing this design exists to avoid."""
    code, _, err = _run(["keygen", "--key", str(tmp_path / "k.pem")], capsys)

    assert code == 2
    # The sentence that explains WHY, not the generic usage — dropping the
    # check leaves `flags["--as"]` raising KeyError, which also exits 2 and
    # also prints a usage line containing "--as". A test that accepted that
    # would pass while the requirement was gone.
    assert "the handle this key is trusted under" in err
    assert not (tmp_path / "k.pem").exists()


# ─── the error paths, which are the ones that get read ───────────────


def test_a_signing_key_of_the_wrong_kind_is_refused():
    """Someone points `--key` at an RSA key, or at a TLS key lying around.
    It has to say which, or they will conclude the certificate machinery is
    broken rather than that they picked the wrong file."""
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import rsa

    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        encoding=ser.Encoding.PEM,
        format=ser.PrivateFormat.PKCS8,
        encryption_algorithm=ser.NoEncryption(),
    )

    with pytest.raises(GrantError, match="ed25519"):
        issue(Grant(tool="t", max_bytes=1, publish_until=None), private_key=wrong)


def test_a_certificate_that_is_not_even_base64_says_it_is_damaged(keys):
    # Truncated in a mail client, or a stray character on paste. `"a"` is not
    # a decodable length, which is what actually raises.
    _, public = keys

    with pytest.raises(GrantError, match="damaged"):
        verify("a.b", public_keys=public, tool="t")


def test_a_properly_signed_certificate_with_the_wrong_shape_inside(keys):
    """Signed by a key we trust, so it gets past the signature — and then the
    payload is missing a field. Only we can produce this, by shipping a bug;
    it must still read as "malformed", not as a KeyError traceback."""
    import base64 as b64

    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private, public = keys
    key = ser.load_pem_private_key(private, password=None)
    # `load_pem_private_key` returns a union of every key kind; narrow it the
    # same way `issue` does, or `sign` is ambiguous.
    assert isinstance(key, ed25519.Ed25519PrivateKey)
    payload = json.dumps({"tool": "t"}).encode()  # no max_bytes, no expires
    enc = lambda raw: b64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731

    with pytest.raises(GrantError, match="malformed"):
        verify(
            f"{enc(payload)}.{enc(key.sign(payload))}",
            public_keys=public,
            tool="t",
        )


def test_a_stray_argument_is_reported_rather_than_swallowed(capsys):
    code, _, err = _run(["issue", "pdf-extract"], capsys)

    assert code == 2
    assert "pdf-extract" in err


def test_an_expiry_that_is_not_a_date_is_reported(tmp_path, capsys):
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key), "--as", "alice"], capsys)

    code, _, err = _run(
        ["issue", "--tool", "t", "--max-mb", "1", "--publish-until", "soon", "--key", str(key)],
        capsys,
    )

    assert code == 2
    assert "soon" in err

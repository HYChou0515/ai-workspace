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
    issue,
    keypair,
    limit_for,
    verify,
)


@pytest.fixture
def keys() -> tuple[bytes, str]:
    """A throwaway signing keypair: the private key bytes and the public key
    in the form that gets committed."""
    return keypair()


def test_a_certificate_says_which_tool_how_big_and_until_when(keys):
    private, public = keys
    token = issue(
        Grant(tool="pdf-extract", max_bytes=300 * 1024 * 1024, expires=date(2026, 9, 1)),
        private_key=private,
    )

    granted = verify(token, public_keys=[public], tool="pdf-extract", today=date(2026, 8, 1))

    assert granted.tool == "pdf-extract"
    assert granted.max_bytes == 300 * 1024 * 1024
    assert granted.expires == date(2026, 9, 1)


def test_a_certificate_fits_on_one_line_so_it_can_be_pasted_into_a_reply(keys):
    """Issuing happens out of band: an author asks, the platform team reads
    their tool, and answers. Whatever comes out of that has to survive being
    pasted into mail, so it is one line of ASCII with no wrapping."""
    private, _ = keys

    token = issue(Grant(tool="t", max_bytes=1, expires=None), private_key=private)

    assert "\n" not in token
    assert token.isascii()


def test_a_certificate_for_another_tool_is_refused(keys):
    """Otherwise one raised limit is every author's raised limit: the token is
    not a secret, and it travels in a manifest anyone can read."""
    private, public = keys
    token = issue(Grant(tool="pdf-extract", max_bytes=10, expires=None), private_key=private)

    with pytest.raises(GrantError, match="pdf-extract"):
        verify(token, public_keys=[public], tool="wafer-history", today=date(2026, 8, 1))


def test_an_expired_certificate_is_refused_and_says_when_it_lapsed(keys):
    """The expiry is the only way a grant ever ends: the author holds it and
    verifies it offline, so nothing we do later can reach it."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, expires=date(2026, 9, 1)), private_key=private)

    with pytest.raises(GrantError, match="2026-09-01"):
        verify(token, public_keys=[public], tool="t", today=date(2026, 9, 2))


def test_a_certificate_is_still_valid_on_the_day_it_expires(keys):
    # An expiry date the holder cannot use for its whole last day is a date
    # that means something other than what it says.
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, expires=date(2026, 9, 1)), private_key=private)

    assert verify(token, public_keys=[public], tool="t", today=date(2026, 9, 1)).max_bytes == 10


def test_a_certificate_can_be_issued_with_no_expiry_at_all(keys):
    """Some tools are permanently large and re-issuing every year is a chore
    that gets automated into meaninglessness. `never` is spelled out rather
    than being what you get by leaving the field off."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, expires=None), private_key=private)

    granted = verify(token, public_keys=[public], tool="t", today=date(2099, 1, 1))

    assert granted.expires is None


def test_raising_the_number_after_it_was_signed_is_refused(keys):
    """The whole point of signing. Someone edits the ceiling in a token they
    were given; the signature stops covering what the payload now says."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, expires=None), private_key=private)
    payload, _, signature = token.partition(".")
    import base64

    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["max_bytes"] = 99_000_000_000
    forged = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")

    with pytest.raises(GrantError, match="signature"):
        verify(f"{forged}.{signature}", public_keys=[public], tool="t", today=date(2026, 8, 1))


def test_a_certificate_signed_by_someone_else_is_refused(keys):
    private, _ = keys
    _, other_public = keypair()
    token = issue(Grant(tool="t", max_bytes=10, expires=None), private_key=private)

    with pytest.raises(GrantError, match="signature"):
        verify(token, public_keys=[other_public], tool="t", today=date(2026, 8, 1))


def test_any_of_the_trusted_keys_may_have_signed_it(keys):
    """Rotation: a new key starts signing while certificates from the old one
    are still in the field. Neither the author nor the platform team sees this
    happen."""
    private, public = keys
    _, retired = keypair()
    token = issue(Grant(tool="t", max_bytes=10, expires=None), private_key=private)

    assert verify(token, public_keys=[retired, public], tool="t", today=date(2026, 8, 1))


def test_a_certificate_is_refused_when_no_key_is_trusted_yet(keys):
    """The state this ships in, until someone runs keygen. It must read as
    "we have not set this up" rather than as the author's mistake."""
    private, _ = keys
    token = issue(Grant(tool="t", max_bytes=10, expires=None), private_key=private)

    with pytest.raises(GrantError, match="no trusted"):
        verify(token, public_keys=[], tool="t", today=date(2026, 8, 1))


@pytest.mark.parametrize("bad", ["", "not-a-token", "a.b.c", "!!!.???"])
def test_a_damaged_certificate_says_so_instead_of_crashing(bad, keys):
    _, public = keys

    with pytest.raises(GrantError):
        verify(bad, public_keys=[public], tool="t", today=date(2026, 8, 1))


# ─── what a caller actually asks ─────────────────────────────────────


def test_a_tool_with_no_certificate_gets_the_default_limit(keys):
    _, public = keys

    assert limit_for(tool="t", token=None, public_keys=[public], today=date(2026, 8, 1)) == (
        DEFAULT_MAX_BYTES
    )


def test_a_tool_with_a_certificate_gets_what_it_was_granted(keys):
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=300 * 1024 * 1024, expires=None), private_key=private)

    limit = limit_for(tool="t", token=token, public_keys=[public], today=date(2026, 8, 1))

    assert limit == 300 * 1024 * 1024


def test_the_default_limit_is_the_number_the_authoring_guide_quotes():
    """The guide tells authors each dependency ships inside a ~150MB artifact.
    If the enforced number were a different one, the sentence they plan
    against would be a lie."""
    assert DEFAULT_MAX_BYTES == 150 * 1024 * 1024


def test_an_unusable_certificate_is_reported_rather_than_ignored(keys):
    """Falling back to the default here would tell an author their bundle is
    too big, when what actually happened is that their grant ran out. They
    would go delete dependencies to fix a paperwork problem."""
    private, public = keys
    token = issue(Grant(tool="t", max_bytes=10, expires=date(2026, 1, 1)), private_key=private)

    with pytest.raises(GrantError, match="2026-01-01"):
        limit_for(tool="t", token=token, public_keys=[public], today=date(2026, 8, 1))


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

    code, printed, _ = _run(["keygen", "--key", str(key)], capsys)
    assert code == 0
    public = next(
        line.strip().strip('",') for line in printed.splitlines() if line.strip().startswith('"')
    )

    code, token, _ = _run(
        ["issue", "--tool", "pdf-extract", "--max-mb", "300", "--expires", "2026-09-01"]
        + ["--key", str(key)],
        capsys,
    )

    assert code == 0
    granted = verify(
        token.strip(), public_keys=[public], tool="pdf-extract", today=date(2026, 8, 1)
    )
    assert granted.max_bytes == 300 * 1024 * 1024
    assert granted.expires == date(2026, 9, 1)


def test_the_private_key_is_written_readable_only_by_its_owner(tmp_path, capsys):
    key = tmp_path / "tool-grant.pem"

    _run(["keygen", "--key", str(key)], capsys)

    assert key.stat().st_mode & 0o077 == 0, oct(key.stat().st_mode)


def test_keygen_refuses_to_overwrite_an_existing_key(tmp_path, capsys):
    """Overwriting is not a lost file — it silently invalidates every
    certificate ever issued, and the tools holding them fail at their next
    build with a signature error nobody will connect to this."""
    key = tmp_path / "tool-grant.pem"
    _run(["keygen", "--key", str(key)], capsys)
    before = key.read_bytes()

    code, _, err = _run(["keygen", "--key", str(key)], capsys)

    assert code == 2
    assert "exists" in err
    assert key.read_bytes() == before


def test_issuing_forever_has_to_be_asked_for_in_those_words(tmp_path, capsys):
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key)], capsys)

    code, token, _ = _run(
        ["issue", "--tool", "t", "--max-mb", "300", "--expires", "never", "--key", str(key)],
        capsys,
    )

    assert code == 0
    payload = json.loads(
        __import__("base64").urlsafe_b64decode(
            token.split(".")[0] + "=" * (-len(token.split(".")[0]) % 4)
        )
    )
    assert payload["expires"] == "never"


def test_issuing_without_an_expiry_is_refused(tmp_path, capsys):
    """`never` is a decision. Leaving the flag off must not quietly make one."""
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key)], capsys)

    code, _, err = _run(["issue", "--tool", "t", "--max-mb", "300", "--key", str(key)], capsys)

    assert code == 2
    assert "--expires" in err


def test_a_certificate_is_printed_alone_so_it_can_be_copied(tmp_path, capsys):
    key = tmp_path / "k.pem"
    _run(["keygen", "--key", str(key)], capsys)

    _, printed, _ = _run(
        ["issue", "--tool", "t", "--max-mb", "1", "--expires", "never", "--key", str(key)], capsys
    )

    assert len(printed.strip().splitlines()) == 1


def test_an_unreadable_key_is_reported_rather_than_traced(tmp_path, capsys):
    code, _, err = _run(
        ["issue", "--tool", "t", "--max-mb", "1", "--expires", "never"]
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
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "workspace_app/tooling/grant.py" in done.stdout, done.stdout

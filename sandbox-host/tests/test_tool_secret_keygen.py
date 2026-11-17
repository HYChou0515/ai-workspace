"""`python -m sandbox_host.tool_secret_key <file>`: the operator makes this
deployment's key pair (docs/plan-tool-identity-secrets.md P4).

The private half goes to a file only its owner can read — it becomes the k8s
secret the host mounts; the public half is printed, to be added to
`TOOL_SECRET_RECIPIENTS` on the GitLab group.
"""

from __future__ import annotations

import stat

import pytest

from sandbox_host.tool_secret_key import main
from sandbox_host.tool_secrets import (
    parse_recipients,
    private_key_from_text,
    public_key_text,
)


def test_it_writes_a_private_key_only_its_owner_can_read(tmp_path, capsys):
    path = tmp_path / "tool-secret.key"
    assert main([str(path)]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    key = private_key_from_text(path.read_text())
    printed = capsys.readouterr().out.strip()
    (public,) = parse_recipients(printed)
    assert public_key_text(public) == public_key_text(key.public_key())


def test_it_never_overwrites_an_existing_key(tmp_path, capsys):
    """Replacing a live key strands every tool sealed to it until each is
    rebuilt — that must be a decision, not a re-run of a setup step."""
    path = tmp_path / "tool-secret.key"
    path.write_text("keep me\n")
    assert main([str(path)]) != 0
    assert path.read_text() == "keep me\n"
    assert "exists" in capsys.readouterr().err


def test_usage_without_a_path(capsys):
    with pytest.raises(SystemExit):
        main([])

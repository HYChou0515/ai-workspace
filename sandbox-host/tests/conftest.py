"""Shared setup for the third-party tool tests (#674).

Every tool that runs here carries a certificate the platform signed — that is
what admits it, and what says which tool it is. So the tests are set up the
way a deployment is: a trusted key exists, and artifacts are certified.

A test that wants to see a refusal takes the certificate away on purpose,
which reads as the exception it is.
"""

from __future__ import annotations

from datetime import date

import pytest

from sandbox_host import grant as grant_mod

_PRIVATE, _PUBLIC = grant_mod.keypair()


def certify(tool: str, *, max_mb: int = 150, expires: date | None = None) -> str:
    """A certificate for `tool`, signed by the key the tests trust."""
    return grant_mod.issue(
        grant_mod.Grant(tool=tool, max_bytes=max_mb * 1024 * 1024, publish_until=expires),
        private_key=_PRIVATE,
    )


@pytest.fixture(autouse=True)
def _trusted_key(monkeypatch):
    """The deployment's key list, as every deployment has one.

    Autouse because an empty list is not a neutral starting point — it is the
    state a platform is in before anyone has run `keygen`, and every tool is
    refused in it."""
    monkeypatch.setattr(grant_mod, "TRUSTED_KEYS", {"tests": _PUBLIC})

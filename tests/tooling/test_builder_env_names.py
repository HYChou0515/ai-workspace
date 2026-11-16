"""A name the host would drop cannot be declared (review round 2, M2).

The sandbox host scrubs, from a third-party tool's environment, every user-set
name that decides where code loads from, where traffic goes or whom it trusts
(`tool_env.py`). A tool that DECLARED such a name in `env.json` would have its
users asked to fill it in — and then never receive it, with nobody told. So the
builder refuses it in the author's own CI, naming it.

`tool_env.py` is the same file on both sides, so the rule the builder checks is
the rule the host applies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tooling.test_builder import _BUILDER, _fake_bundle, _source
from tests.tooling.test_builder_secrets import _with_env_json
from workspace_app.tooling.builder import BuildError, build_artifact


def _build(tmp_path: Path, entries: list[dict]) -> None:
    build_artifact(
        source=_with_env_json(_source(tmp_path), entries),
        out=tmp_path / "dist",
        builder_id=_BUILDER,
        build_bundle=_fake_bundle({"trend": "t"}),
        smoke_check=lambda _dist: None,
    )


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "GIT_TOKEN", "PYTHONPATH", "ssl_cert_file"])
def test_a_name_the_host_drops_cannot_be_declared(tmp_path, name):
    with pytest.raises(BuildError, match=name):
        _build(tmp_path, [{"name": name}])


def test_an_ordinary_name_still_builds(tmp_path):
    _build(tmp_path, [{"name": "ERP_TOKEN"}, {"name": "GITLAB_TOKEN"}])


def test_a_secret_is_not_a_user_name_and_is_not_checked_as_one(tmp_path, monkeypatch):
    """The tool's own credentials are added by the host after the scrub; only
    the names a USER fills in can be dropped."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from workspace_app.tooling.tool_secrets import public_key_text

    key = X25519PrivateKey.generate()
    monkeypatch.setenv("GIT_DEPLOY_KEY", "x")
    monkeypatch.setenv("TOOL_SECRET_RECIPIENTS", public_key_text(key.public_key()))
    _build(tmp_path, [{"name": "GIT_DEPLOY_KEY", "secret": True}])


def test_both_copies_of_the_rule_are_the_same_file():
    repo = Path(__file__).resolve().parents[2]
    app = repo / "src" / "workspace_app" / "tooling" / "tool_env.py"
    host = repo / "sandbox-host" / "src" / "sandbox_host" / "tool_env.py"
    assert host.read_bytes() == app.read_bytes(), (
        "sandbox-host/src/sandbox_host/tool_env.py has drifted from the app's copy — "
        "copy it across; the builder checks the rule the host applies"
    )

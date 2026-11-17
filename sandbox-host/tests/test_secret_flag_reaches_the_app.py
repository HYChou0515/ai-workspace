"""Which names are secret travels every hop to the app
(docs/plan-tool-identity-secrets.md P4).

The app never reads a manifest: it learns a tool's `env` from `/tools/resolve`,
which is built from a `ResolvedTool`, which an outage rebuilds from the host's
remembered note. A hop that dropped `secret` would hand the app the machine
password's NAME as an ordinary variable — and the environment panel would offer
the user a field to type it into.
"""

from __future__ import annotations

from pathlib import Path

from sandbox_host.artifact import CommandSpec, EnvSpec
from sandbox_host.tool_resolve import ResolvedTool

from .test_tool_resolve import _MANIFEST_URL, _bundle, _manifest, _resolver, _Wire
from .test_tools_endpoint import _client, _Resolver

_ENV = [
    {"name": "MACHINE_PASSWORD", "description": "", "required": None, "secret": True},
    {"name": "ERP_BASE", "description": "site", "required": False},
]


async def test_the_resolve_reply_says_which_names_are_secret() -> None:
    resolver = _Resolver(
        erp=ResolvedTool(
            name="erp",
            sha="a" * 64,
            version="1.0",
            commands=(CommandSpec("q", "q", {"type": "object"}),),
            stale=False,
            env=(EnvSpec("MACHINE_PASSWORD", secret=True), EnvSpec("ERP_BASE", "site", False)),
        )
    )
    async with _client(resolver) as c:
        r = await c.post("/tools/resolve", json={"tools": {"erp": "https://g/m"}})
    assert r.json()["tools"]["erp"]["env"] == _ENV


def test_a_resolve_carries_the_flag_from_the_manifest(tmp_path: Path) -> None:
    data = _bundle()
    wire = _Wire(manifest=_manifest(data, env=_ENV), bundle=data)
    resolved = _resolver(tmp_path, wire).resolve("wafer-history", _MANIFEST_URL)
    assert resolved.env is not None
    assert [e.secret for e in resolved.env] == [True, False]


def test_the_remembered_copy_keeps_the_flag(tmp_path: Path) -> None:
    """An outage serves the remembered note; losing the flag there would show
    the field exactly when nothing else can be checked."""
    data = _bundle()
    _resolver(tmp_path, _Wire(manifest=_manifest(data, env=_ENV), bundle=data)).resolve(
        "wafer-history", _MANIFEST_URL
    )
    fallen_back = _resolver(tmp_path, _Wire()).resolve("wafer-history", _MANIFEST_URL)
    assert fallen_back.stale is True
    assert fallen_back.env is not None
    assert [e.secret for e in fallen_back.env] == [True, False]

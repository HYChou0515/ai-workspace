"""Regression probe for the R1 fix — does ESCALATED_FIELDS over-gate?

THROWAWAY. Delete before commit.
"""

from __future__ import annotations

from workspace_app.config.schema import PerUserResources
from workspace_app.perm.model import Permission

from .test_item_resources import (
    FOUR_CORES,
    WHO,
    _app,
    _mk,
    _restricted,
)


def test_probe_whole_object_put_by_write_meta_collaborator():
    """A collaborator with write_meta does a whole-object PUT that names NEITHER
    escalated field. Before the fix this was a 200."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(
            spec,
            "owner-alice",
            permission=_restricted(read_meta=["user:bob"], write_meta=["user:bob"]),
        )
        WHO["id"] = "bob"
        got = client.get(f"/rca-investigation/{item}")
        print("GET", got.status_code, got.text[:600])
        body = got.json()
        data = body.get("data", body)
        print("DATA KEYS", sorted(data) if isinstance(data, dict) else type(data))
        put = client.put(f"/rca-investigation/{item}", json=data)
        print("PUT", put.status_code, put.text[:600])
        assert put.status_code == 200, f"over-gated: {put.text}"


def test_probe_owner_whole_object_put():
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))
        got = client.get(f"/rca-investigation/{item}")
        data = got.json().get("data", got.json())
        put = client.put(f"/rca-investigation/{item}", json=data)
        print("OWNER PUT", put.status_code, put.text[:600])


def test_probe_whole_object_put_smuggling_a_size():
    """With the attribute name corrected, does the whole-object door STILL close?"""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(
            spec,
            "owner-alice",
            permission=_restricted(read_meta=["user:bob"], write_meta=["user:bob"]),
        )
        WHO["id"] = "bob"
        data = client.get(f"/rca-investigation/{item}").json()["data"]
        data["sandbox_cpu_cores"] = 999.0
        put = client.put(f"/rca-investigation/{item}", json=data)
        print("SMUGGLE PUT", put.status_code, put.text[:200])
        assert put.status_code == 403, f"whole-object door OPEN: {put.text}"


def test_probe_public_item_whole_object_put_by_stranger():
    """The real attack shape from finding 1, but through PUT instead of PATCH."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))
        WHO["id"] = "carol"
        data = client.get(f"/rca-investigation/{item}").json()["data"]
        data["sandbox_cpu_cores"] = 999.0
        put = client.put(f"/rca-investigation/{item}", json=data)
        print("PUBLIC SMUGGLE PUT", put.status_code, put.text[:200])
        assert put.status_code == 403, f"public whole-object door OPEN: {put.text}"

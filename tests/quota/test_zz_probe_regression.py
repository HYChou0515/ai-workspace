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


def test_probe_list_item_routes():
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        app = client.app
        for r in app.routes:
            path = getattr(r, "path", "")
            if "rca-investigation" in path:
                print(sorted(getattr(r, "methods", []) or []), path)


def test_probe_memory_zero_reaches_the_sandbox_spec():
    """The 'sharp end' the author named — still reachable through door 3 and 4."""
    from .test_item_resources import _wake

    # door 3: auto-CRUD create
    with _app(PerUserResources(cpu=4.0, memory="4G"), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        made = client.post(
            "/rca-investigation",
            json={"title": "x", "owner": "alice", "sandbox_memory_bytes": 0},
        )
        item = made.json()["resource_id"]
        _wake(client, item)
        print("AUTOCRUD memory in spec:", sandbox.specs[-1].memory_bytes)

    # door 4: RFC 6902 json-patch
    hdr = {"Content-Type": "application/json-patch+json"}
    with _app(PerUserResources(cpu=4.0, memory="4G"), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))
        WHO["id"] = "carol"
        got = client.patch(
            f"/rca-investigation/{item}",
            json=[{"op": "replace", "path": "/sandbox_memory_bytes", "value": 0}],
            headers=hdr,
        )
        print("JSONPATCH mem0 ->", got.status_code)
        _wake(client, item)
        print("JSONPATCH memory in spec:", sandbox.specs[-1].memory_bytes)


def test_probe_json_patch_permission_too():
    """Is the RFC 6902 hole NEW, or does the original `permission` gate have it?"""
    hdr = {"Content-Type": "application/json-patch+json"}
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))
        WHO["id"] = "carol"
        ops = [
            {
                "op": "replace",
                "path": "/permission/change_permission",
                "value": ["user:carol"],
            }
        ]
        got = client.patch(f"/rca-investigation/{item}", json=ops, headers=hdr)
        after = client.get(f"/rca-investigation/{item}").json()["data"]
        print("JSONPATCH PERMISSION ->", got.status_code, got.text[:200])
        print("STORED change_permission:", after["permission"]["change_permission"])


def test_probe_json_patch_needs_write_meta():
    """Control: is json-patch gated at ALL? A private item carol cannot see."""
    hdr = {"Content-Type": "application/json-patch+json"}
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice", permission=_restricted(read_meta=["user:carol"]))
        WHO["id"] = "carol"
        ops = [{"op": "replace", "path": "/sandbox_cpu_cores", "value": 999.0}]
        got = client.patch(f"/rca-investigation/{item}", json=ops, headers=hdr)
        print("NO WRITE_META JSONPATCH ->", got.status_code, got.text[:200])


def test_probe_json_patch_shapes():
    """RFC 6902 shapes: does every one of them hit ESCALATED_FIELDS?"""
    hdr = {"Content-Type": "application/json-patch+json"}
    shapes = {
        "direct": [{"op": "replace", "path": "/sandbox_cpu_cores", "value": 999.0}],
        "add": [{"op": "add", "path": "/sandbox_cpu_cores", "value": 999.0}],
        "root_replace_empty": [{"op": "replace", "path": "", "value": None}],
        "copy_into": [{"op": "copy", "from": "/severity", "path": "/sandbox_cpu_cores"}],
    }
    for name, ops in shapes.items():
        with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
            client,
            spec,
            _sb,
        ):
            item = _mk(spec, "alice", permission=Permission(visibility="public"))
            whole = client.get(f"/rca-investigation/{item}").json()["data"]
            body = ops
            if name == "root_replace_empty":
                whole["sandbox_cpu_cores"] = 999.0
                body = [{"op": "replace", "path": "", "value": whole}]
            WHO["id"] = "carol"
            got = client.patch(f"/rca-investigation/{item}", json=body, headers=hdr)
            after = client.get(f"/rca-investigation/{item}").json()["data"]
            print(name, "->", got.status_code, "stored:", after["sandbox_cpu_cores"])


def test_probe_507_store_reads_per_held_item():
    """N+1 lens: how many store reads does the refusal cost per held item?"""
    import workspace_app.api.app as app_mod
    import workspace_app.api.item_authz as authz_mod
    import workspace_app.api.locator as loc_mod
    import workspace_app.apps.resolve as resolve_mod

    counts = {"find": 0, "get_meta": 0, "groups": 0}

    real_find = resolve_mod.find_work_item
    real_groups = authz_mod.groups_of
    real_facts = authz_mod.load_access_facts

    def counted_find(spec, item_id):
        counts["find"] += 1
        return real_find(spec, item_id)

    def counted_groups(spec, user):
        counts["groups"] += 1
        return real_groups(spec, user)

    def counted_facts(spec, item_id):
        counts["get_meta"] += 1
        return real_facts(spec, item_id)

    resolve_mod.find_work_item = counted_find
    loc_mod.find_work_item = counted_find
    authz_mod.find_work_item = counted_find
    app_mod.find_work_item = counted_find
    authz_mod.groups_of = counted_groups
    authz_mod.load_access_facts = counted_facts
    try:
        with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
            client,
            spec,
            _sb,
        ):
            from .test_item_resources import _wake

            held = [_mk(spec, "alice", cpu=0.5) for _ in range(4)]
            for h in held:
                _wake(client, h)
            blocked = _mk(spec, "alice", cpu=2.0)
            for k in counts:
                counts[k] = 0
            got = client.post(f"/a/rca/items/{blocked}/messages", json={"content": "go"})
            print("STATUS", got.status_code)
            print("HOLDING", len(got.json()["detail"].get("holding", [])))
            print("COUNTS", counts)
    finally:
        resolve_mod.find_work_item = real_find
        loc_mod.find_work_item = real_find
        authz_mod.find_work_item = real_find
        app_mod.find_work_item = real_find
        authz_mod.groups_of = real_groups
        authz_mod.load_access_facts = real_facts


def test_probe_local_backend_liveness_is_dir_existence():
    """Finding 10's interaction with the new 409, on the DEFAULT VM backend."""
    import asyncio
    import tempfile

    from workspace_app.api.registry import InvestigationRegistry
    from workspace_app.sandbox.local_process import LocalProcessSandbox
    from workspace_app.sandbox.protocol import SandboxSpec

    async def go():
        with tempfile.TemporaryDirectory() as root:
            import pathlib

            sb = LocalProcessSandbox(root_dir=pathlib.Path(root))
            reg = InvestigationRegistry(sandbox=sb)
            item = "rca-investigation:abc"
            await sb.create(SandboxSpec(), sandbox_id=item)
            print("SESSIONS", dict(reg._sessions))
            print("HAS LIVE (no session, dir exists)", await reg.has_live_sandbox(item))
            print("RUNNING LIST", await sb.running_sandboxes())
            await reg.close_session(item)
            print("HAS LIVE after close_session", await reg.has_live_sandbox(item))

    asyncio.run(go())


def test_probe_wake_then_close_then_resize():
    """The control the author's test did NOT run: an item that HAS been used."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        from .test_item_resources import _wake

        item = _mk(spec, "alice", cpu=4.0)
        _wake(client, item)
        blocked = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})
        print("LIVE RESIZE", blocked.status_code)
        closed = client.delete(f"/me/resources/live/{item}")
        print("CLOSE ENV", closed.status_code, closed.text[:200])
        after = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": 1.0})
        print("AFTER CLOSE RESIZE", after.status_code, after.text[:300])


def test_probe_bad_body_on_a_live_item():
    """Does a bad body now get 409 instead of 422?"""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        from .test_item_resources import _wake

        item = _mk(spec, "alice", cpu=4.0)
        _wake(client, item)
        got = client.put(f"/a/rca/items/{item}/resources", json={"cpu_cores": -5.0})
        print("BAD BODY LIVE", got.status_code, got.text[:200])
        cold = _mk(spec, "alice", cpu=4.0)
        got2 = client.put(f"/a/rca/items/{cold}/resources", json={"cpu_cores": -5.0})
        print("BAD BODY COLD", got2.status_code, got2.text[:200])


def test_probe_autocrud_create_can_set_a_size():
    """The THIRD door: specstar's own POST /rca-investigation, not /a/rca/items."""
    with _app(PerUserResources(cpu=2.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        sandbox,
    ):
        WHO["id"] = "carol"
        made = client.post(
            "/rca-investigation",
            json={
                "title": "smuggled",
                "owner": "alice",
                "sandbox_cpu_cores": 999.0,
                "sandbox_memory_bytes": 0,
            },
        )
        print("AUTOCRUD CREATE", made.status_code, made.text[:400])
        if made.status_code not in (200, 201):
            return
        item = made.json()["resource_id"]
        got = client.get(f"/rca-investigation/{item}").json()["data"]
        print("STORED", got["sandbox_cpu_cores"], got["sandbox_memory_bytes"])
        assert got["sandbox_cpu_cores"] is None, "auto-CRUD create door is OPEN"


def _app_500(limits, *, app_resources):
    """Same wiring, but the client does NOT re-raise — so we see the STATUS a
    browser would get."""
    import contextlib

    from specstar import SpecStar

    from workspace_app.api import ScriptedAgentRunner, create_app
    from workspace_app.filestore.specstar_impl import SpecstarFileStore

    from ..api._client import TestClient as ApiTestClient
    from .test_item_resources import _RecordingSandbox

    @contextlib.contextmanager
    def _go():
        WHO["id"] = "alice"
        spec = SpecStar(default_user=lambda: WHO["id"])
        from workspace_app.resources import make_spec

        spec = make_spec(default_user=lambda: WHO["id"])
        sandbox = _RecordingSandbox(cpu_cores=8.0, memory_bytes=8 * 1024**3)
        app = create_app(
            spec=spec,
            sandbox=sandbox,
            filestore=SpecstarFileStore(spec),
            runner=ScriptedAgentRunner([]),
            get_user_id=lambda: WHO["id"],
            app_resources=app_resources,
            per_user_resources=limits,
        )
        with ApiTestClient(app, raise_server_exceptions=False) as client:
            yield client, spec, sandbox

    return _go()


def test_probe_close_status_a_browser_sees():
    with _app_500(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))
        WHO["id"] = "carol"
        got = client.post(f"/a/rca/items/{item}/close", json={"status": "resolved"})
        print("BROWSER CLOSE STATUS", got.status_code)


def test_probe_close_by_a_write_meta_collaborator():
    """`close_app_item` does a WHOLE-OBJECT rm.update as the acting user."""
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
        got = client.post(f"/a/rca/items/{item}/close", json={"status": "resolved"})
        print("CLOSE", got.status_code, got.text[:400])
        assert got.status_code in (200, 204), f"close broken for collaborator: {got.text}"


def test_probe_close_on_a_public_item_by_a_stranger():
    """Public visibility grants write_meta to anyone, so this was a 204 before."""
    with _app(PerUserResources(cpu=4.0), app_resources={"rca": FOUR_CORES}) as (
        client,
        spec,
        _sb,
    ):
        item = _mk(spec, "alice", permission=Permission(visibility="public"))
        WHO["id"] = "carol"
        got = client.post(f"/a/rca/items/{item}/close", json={"status": "resolved"})
        print("PUBLIC CLOSE", got.status_code, got.text[:400])
        assert got.status_code in (200, 204), f"close broken on public item: {got.text}"


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

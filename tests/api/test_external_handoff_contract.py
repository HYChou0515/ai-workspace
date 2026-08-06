"""#700 — the handoff, played from the CALLING side.

Nothing about this feature is visible in our own UI: the picker lives in the
outside system's page, so "it merged" proves nothing. What has to hold is that
the sequence a legacy site actually issues — list, create or pick, upload,
record — works end to end against routes that already exist.

So this is a contract double: it models the other side's real request sequence,
including the parts we do not control (the exact `sorts` JSON, the raw-body PUT,
the RFC 6902 patch). A test that only asserted our handlers do not crash would
be immune to the regressions that matter here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from workspace_app.agent.config_catalog import AgentConfigCatalog
from workspace_app.agent.context import AgentToolContext
from workspace_app.api import RunDone, create_app
from workspace_app.api.events import AgentEvent
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient

_NEWEST_FIRST = json.dumps(
    [
        {"type": "meta", "key": "updated_time", "direction": "-"},
        # Tiebreaker, not decoration: without a total order, two rows sharing a
        # timestamp can swap between pages, so `offset` paging silently skips
        # one — and an item the picker never shows is one the user re-creates.
        {"type": "meta", "key": "resource_id", "direction": "+"},
    ]
)


class _Runner:
    async def run(self, prompt: str, ctx: AgentToolContext) -> AsyncIterator[AgentEvent]:
        yield RunDone()


class _LegacySite:
    """The calling side, expressed as the four moves it actually makes.

    Written as the caller sees the platform — no imports of our internals, no
    resource-manager peeking — so if a route, parameter name or status code
    drifts, this breaks in the same way the real integration would.
    """

    def __init__(self, client: TestClient, slug: str = "rca") -> None:
        self._c = client
        self._slug = slug

    def list_candidates(self, limit: int = 100) -> list[dict]:
        """The picker's only read: newest-touched first, explicitly capped.

        Reads the ENVELOPE listing, not the `/data` one. `/data` returns the
        struct alone — no resource id — so a picker built on it can render rows
        and then do nothing with the one the user clicks. The id lives in
        `revision_info`, which is also where this platform's own frontend reads
        it from (`web/src/api/real.ts`).
        """
        r = self._c.get(
            "/rca-investigation",
            params={"limit": limit, "sorts": _NEWEST_FIRST},
        )
        assert r.status_code == 200, r.text
        return [{"id": row["revision_info"]["resource_id"], **row["data"]} for row in r.json()]

    def already_absorbed(self, items: list[dict], ref: str) -> list[str]:
        """Answered from the page already fetched — never a query (the field is
        deliberately un-indexed, so a query would be silently wrong on SQL)."""
        return [it["title"] for it in items if ref in it["external_refs"]]

    def open_new_item(self, title: str, ref: str) -> str:
        r = self._c.post(
            f"/a/{self._slug}/items",
            json={
                "title": title,
                "external_refs": [ref],
                # Explicit, because the default is private — an item nobody else
                # can see cannot be the thing colleagues converge on.
                "permission": {"visibility": "public"},
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["resource_id"]

    def upload(self, item_id: str, ref: str, name: str, body: bytes) -> None:
        """One folder per source record, so several handoffs cannot collide."""
        folder = ref.replace(":", "-")
        r = self._c.put(f"/a/{self._slug}/items/{item_id}/files/{folder}/{name}", content=body)
        assert r.status_code == 204, r.text

    def record_absorbed(self, item_id: str, ref: str) -> None:
        """Append, never replace — a diff, so a second handoff cannot erase the first."""
        r = self._c.patch(
            f"/rca-investigation/{item_id}",
            json=[{"op": "add", "path": "/external_refs/-", "value": ref}],
        )
        assert r.status_code == 200, r.text

    def files_in(self, item_id: str) -> set[str]:
        r = self._c.get(f"/a/{self._slug}/items/{item_id}/files")
        assert r.status_code == 200, r.text
        return {e["path"] for e in r.json()}


def _app_for(who: dict[str, str]):
    """One store, a switchable caller — the same shape as two people sharing a
    deployment. `default_user` and `get_user_id` read the SAME source so the
    creator we record and the actor we authorize can never diverge."""
    spec = make_spec(default_user=lambda: who["user"])
    return create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=MemoryFileStore(),
        runner=_Runner(),
        agent_config_catalog=AgentConfigCatalog(),
        get_user_id=lambda: who["user"],
    )


def test_two_analyses_from_the_legacy_site_converge_on_one_item():
    """The whole point of #700: pressing the button twice for one real problem
    lands both analyses in ONE workspace instead of sprawling into two."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))

    # First analysis: nothing exists yet, so the user opens an item.
    assert legacy.list_candidates() == []
    item_id = legacy.open_new_item("Oven drift", "legacy-rca:1")
    legacy.upload(item_id, "legacy-rca:1", "readings.csv", b"t,v\n1,9\n")

    # Second analysis, same real-world problem: the user picks that item.
    candidates = legacy.list_candidates()
    assert [c["title"] for c in candidates] == ["Oven drift"]
    assert legacy.already_absorbed(candidates, "legacy-rca:2") == []
    # Act on the row the USER CLICKED — the id has to come out of the listing,
    # not out of a create response the picker never sees. This is the move that
    # makes or breaks the integration, so the double must play it.
    picked = candidates[0]["id"]
    assert picked == item_id
    legacy.upload(picked, "legacy-rca:2", "pareto.png", b"\x89PNG")
    legacy.record_absorbed(picked, "legacy-rca:2")

    # One item holds both analyses, each in its own folder.
    absorbed = legacy.list_candidates()[0]["external_refs"]
    assert absorbed == ["legacy-rca:1", "legacy-rca:2"]
    assert legacy.files_in(item_id) >= {
        "/legacy-rca-1/readings.csv",
        "/legacy-rca-2/pareto.png",
    }


def test_a_second_press_of_the_same_analysis_is_recognisable_as_already_taken():
    """Decision 3: same record, same item ⇒ do nothing. The caller must be able
    to tell BEFORE uploading, or the user transfers megabytes for nothing."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))
    legacy.open_new_item("Oven drift", "legacy-rca:1")

    assert legacy.already_absorbed(legacy.list_candidates(), "legacy-rca:1") == ["Oven drift"]


def test_absorbing_an_analysis_pulls_the_item_back_to_the_front_of_the_page():
    """Decision 12 caps the listing, which re-opens the sprawl risk from the
    side: an item past the cap is invisible, so the user opens another one.
    Sorting by *last touched* is what closes it — an item being actively worked
    is pushed back to the front — so the ordering is load-bearing, not cosmetic.
    Created-time order would leave it buried exactly when it matters most."""
    who = {"user": "alice"}
    legacy = _LegacySite(TestClient(_app_for(who)))
    older = legacy.open_new_item("Oven drift", "legacy-rca:1")
    legacy.open_new_item("Coater streaks", "legacy-rca:2")

    assert [c["title"] for c in legacy.list_candidates()] == ["Coater streaks", "Oven drift"]

    legacy.record_absorbed(older, "legacy-rca:3")  # a second analysis lands on the older item

    assert [c["title"] for c in legacy.list_candidates()] == ["Oven drift", "Coater streaks"]


def test_a_colleague_sees_the_item_so_convergence_survives_across_people():
    """Convergence has to hold between people, not just within one account —
    an item a colleague cannot see is one they will silently duplicate."""
    who = {"user": "alice"}
    app = _app_for(who)
    _LegacySite(TestClient(app)).open_new_item("Oven drift", "legacy-rca:1")

    who["user"] = "bob"
    seen = _LegacySite(TestClient(app)).list_candidates()

    assert [c["title"] for c in seen] == ["Oven drift"]

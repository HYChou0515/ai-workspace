"""#605: the app chat's per-chat disclosure toggle.

The composer sends ``disclosure`` on the message body; ChatSendService threads
it through the ``_run_subagent_with_depth`` closure to the bridge, which gates
the probe universe the KB sub-agent receives. False ⇒ the sub-agent sees an
empty ``discoverable_collection_ids`` (probe skipped, faster); absent ⇒ the
operator default (on) — and the universe covers collections the app chat never
picked, the #605 P2 point.
"""

from __future__ import annotations

from workspace_app.api import create_app
from workspace_app.api.events import MessageDelta
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.perm import Permission
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


def _discoverable_seen_for(disclosure: bool | None) -> tuple[list, str]:
    spec = make_spec(default_user="u")
    iid = register_rca_item(spec)
    rm = spec.get_resource_manager(Collection)
    with rm.using("someone-else"):
        unpicked = rm.create(
            Collection(name="Fab-Yield", permission=Permission(visibility="restricted"))
        ).resource_id
    seen: list = []

    class _Runner:
        async def run(self, prompt, ctx):  # noqa: ANN001 — test double
            if ctx.sandbox is None:  # the KB sub-agent turn
                seen.append(list(ctx.discoverable_collection_ids))
                yield MessageDelta(text="kb answer")
                return
            # The app turn consults the KB the way ask_knowledge_base does:
            # forwarding the parent turn's withheld accumulator as the sink.
            await ctx.run_subagent(  # type: ignore[misc]
                "kb_chat", "q", withheld_sink=ctx.withheld_collection_ids
            )
            yield MessageDelta(text="done")

    app = create_app(
        spec=spec, sandbox=MockSandbox(), filestore=MemoryFileStore(), runner=_Runner()
    )
    client = TestClient(app)
    body: dict = {"content": "q"}
    if disclosure is not None:
        body["disclosure"] = disclosure
    client.post(f"/a/rca/items/{iid}/messages", json=body)
    return seen, unpicked


def test_app_chat_disclosure_off_skips_the_probe():
    """The per-chat toggle: disclosure=false ⇒ the KB sub-agent gets an empty
    probe universe even though a discoverable collection exists."""
    seen, _ = _discoverable_seen_for(False)
    assert seen == [[]]


def test_app_chat_probes_unpicked_collections_by_default():
    """Default (operator on, no toggle): the sub-agent's universe covers a
    restricted collection the app chat never picked (#605 P2, app side)."""
    seen, unpicked = _discoverable_seen_for(None)
    assert len(seen) == 1
    assert unpicked in seen[0]

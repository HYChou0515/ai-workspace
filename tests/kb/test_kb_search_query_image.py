"""kb_search runs an image→image arm when the turn carries a query image (#519).

The KB agent has no workspace, so it can't be handed an image *path*. Instead
the query image rides the turn context: a user-attached image (the #514
transient-image path) is decoded once by the API and set on
`AgentToolContext.query_image`; kb_search passes it straight to
`retriever.search(query_image=...)`. Nothing to read, no sync/async file I/O —
the bytes are already in hand.
"""

from __future__ import annotations

from workspace_app.agent import AgentToolContext, kb_search_impl


class _SpyRetriever:
    """Records the query_image kb_search forwards, and returns no passages."""

    def __init__(self) -> None:
        self.query_image_seen = "unset"

    def search(self, query, collection_ids, on_progress=None, **kw):
        self.query_image_seen = kw.get("query_image")
        return []

    # kb_search only calls .search on this path; disclosure probe is off by
    # default (no withheld collections).


def _ctx(retriever, **kw):
    from agents import RunContextWrapper

    return RunContextWrapper(AgentToolContext(retriever=retriever, collection_ids=["c1"], **kw))


def test_kb_search_forwards_the_turns_query_image():
    r = _SpyRetriever()
    kb_search_impl(_ctx(r, query_image=b"PNGBYTES"), "a defect like this")
    assert r.query_image_seen == b"PNGBYTES"


def test_kb_search_without_a_query_image_forwards_none():
    r = _SpyRetriever()
    kb_search_impl(_ctx(r), "plain text search")
    assert r.query_image_seen is None

"""Default-chat resolution for multi-chat items (Phase 6, manual §3).

Item-level (no chat_id) endpoints operate on an implicit *default chat*: the
earliest-born **free** chat (no driving workflow run). Workflow chats are never the
default; existing pre-multi-chat conversations (no birth stamp) stay the default."""

from workspace_app.api.chats import resolve_default_conversation
from workspace_app.resources import Conversation, make_spec


def _conv_rm():
    return make_spec().get_resource_manager(Conversation)


def test_resolve_default_creates_a_free_chat_and_is_idempotent():
    rm = _conv_rm()
    rid, conv = resolve_default_conversation(rm, "it")
    assert conv.run_id is None  # the default is a free chat
    assert conv.item_id == "it"
    rid2, _ = resolve_default_conversation(rm, "it")
    assert rid2 == rid  # resolves the SAME default, never a fresh chat


def test_resolve_default_picks_earliest_free_chat_skipping_workflow_chats():
    rm = _conv_rm()
    # A workflow chat born first must NOT win — it is run-driven, not the default.
    rm.create(Conversation(item_id="it", run_id="run-x", created_ms=1))
    later = rm.create(Conversation(item_id="it", title="b", created_ms=50))
    earlier = rm.create(Conversation(item_id="it", title="a", created_ms=10))
    rid, conv = resolve_default_conversation(rm, "it")
    assert conv.run_id is None
    assert rid == earlier.resource_id  # earliest FREE chat, workflow chat skipped
    assert rid != later.resource_id


def test_resolve_default_prefers_a_legacy_unstamped_conversation():
    """A pre-Phase-6 conversation has no `created_ms`; it predates every stamped
    chat, so it stays the default even after newer free chats are added."""
    rm = _conv_rm()
    legacy = rm.create(Conversation(item_id="it"))  # created_ms None
    rm.create(Conversation(item_id="it", title="new", created_ms=999))
    rid, _ = resolve_default_conversation(rm, "it")
    assert rid == legacy.resource_id


def test_resolve_default_scoped_per_item():
    rm = _conv_rm()
    a, _ = resolve_default_conversation(rm, "item-a")
    b, _ = resolve_default_conversation(rm, "item-b")
    assert a != b  # each item gets its own default chat

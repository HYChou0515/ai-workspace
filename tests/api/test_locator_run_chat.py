"""A run's chat is a real conversation, and it never outlives a run that failed.

`chat_id` is not a label. `workflow_exec.drive_turn` looks it up, and one that
resolves to nothing falls back to the item's DEFAULT chat — so a run started
from a page would take the user's own chat history as its context and append its
turns there, in a conversation nobody opened it from. The same id is what
`active_run_for_chat` matches on, so an invented one also exempts that caller
from the one-run-per-item rule without saying so.

The other half is the cleanup. A conversation with no `run_id` is a FREE chat,
and the earliest free chat is what the item opens as its default — so a chat
left behind by a run that never started does not merely litter, it can become
the default conversation for everyone on the item, once per retry.
"""

from __future__ import annotations

from workspace_app.api.chats import find_default_conversation, list_item_conversations
from workspace_app.api.locator import ItemLocator
from workspace_app.apps.catalog import AppCatalog
from workspace_app.apps.playground.model import PlaygroundItem
from workspace_app.config.schema import Settings
from workspace_app.resources import Conversation, make_spec


def _locator_and_item():
    spec = make_spec(default_user="u")
    locator = ItemLocator(spec, AppCatalog(presets=Settings().agents.presets))
    item_id = (
        spec.get_resource_manager(PlaygroundItem)
        .create(PlaygroundItem(title="t", owner="u", profile="echo"))
        .resource_id
    )
    return spec, locator, item_id


def test_the_chat_it_opens_resolves_to_a_conversation_on_that_item() -> None:
    spec, locator, item_id = _locator_and_item()

    chat_id = locator.open_run_chat(item_id, "judge")

    conv = spec.get_resource_manager(Conversation).get(chat_id).data
    assert isinstance(conv, Conversation)
    assert conv.item_id == item_id
    assert conv.title == "judge"


def test_a_settled_chat_is_a_workflow_chat_and_not_the_items_default() -> None:
    """Linking the run is what takes the chat OUT of the free set. Until then it
    is eligible to become the item's default, which is why the link happens on
    the same request rather than whenever the run first speaks."""
    spec, locator, item_id = _locator_and_item()
    chat_id = locator.open_run_chat(item_id, "judge")

    locator.settle_run_chat(chat_id, "run-7")

    conv = spec.get_resource_manager(Conversation).get(chat_id).data
    assert isinstance(conv, Conversation)
    assert conv.run_id == "run-7"
    assert find_default_conversation(spec.get_resource_manager(Conversation), item_id) is None


def test_a_run_that_never_started_takes_its_chat_with_it() -> None:
    """Asserted on what the item can SEE, not on which error `get` raises.
    specstar deletes softly, so "the row is gone" and "the item no longer has
    this chat" are different claims — and only the second one is the one that
    keeps a refused run from installing a new default conversation."""
    spec, locator, item_id = _locator_and_item()
    chat_id = locator.open_run_chat(item_id, "judge")
    conv_rm = spec.get_resource_manager(Conversation)
    assert find_default_conversation(conv_rm, item_id) is not None  # it was free

    locator.settle_run_chat(chat_id, None)

    assert list_item_conversations(conv_rm, item_id) == []
    assert find_default_conversation(conv_rm, item_id) is None


def test_dropping_a_chat_twice_is_not_an_error() -> None:
    """Two pods can answer the same refusal, and a cleanup that raises turns one
    failed run into a second, unrelated failure — reported to whoever happened
    to be second."""
    _spec, locator, item_id = _locator_and_item()
    chat_id = locator.open_run_chat(item_id, "judge")
    locator.settle_run_chat(chat_id, None)

    locator.settle_run_chat(chat_id, None)  # must not raise

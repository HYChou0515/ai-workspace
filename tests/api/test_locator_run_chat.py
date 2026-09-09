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
    """A cleanup that raises turns one failed run into a SECOND, unrelated
    failure — and the second one is the only one the caller sees.

    Both calls happen inside a single request on a single pod today, so this
    is not a race; it is the ordinary shape of a cleanup path, which must be
    safe to reach twice because it is reached from several places."""
    _spec, locator, item_id = _locator_and_item()
    chat_id = locator.open_run_chat(item_id, "judge")
    locator.settle_run_chat(chat_id, None)

    locator.settle_run_chat(chat_id, None)  # must not raise


def test_a_schedule_keeps_one_conversation_across_its_fires() -> None:
    """A schedule's chat belongs to the SCHEDULE, not to one firing of it.

    Opening a fresh one per fire cost two things at once. `active_run_for_chat`
    keys on the chat, so a brand-new id every time meant a schedule could never
    collide with its own still-running previous fire — the one-run rule stopped
    applying to exactly the entrance that repeats. And the conversations
    accumulate: `every: minutes, n: 1` is 1440 permanent chats a day on one item,
    each loaded by every item-level chat operation.

    Reusing it is also what the thing IS: a recurring report is one thread, the
    way a recurring meeting is.
    """
    spec, locator, item_id = _locator_and_item()

    first, created_first = locator.chat_for_schedule(item_id, "build-report", "wui:i1:abc")
    second, created_second = locator.chat_for_schedule(item_id, "build-report", "wui:i1:abc")

    assert first == second
    assert created_first is True
    assert created_second is False
    assert len(list_item_conversations(spec.get_resource_manager(Conversation), item_id)) == 1


def test_two_schedules_on_one_item_do_not_share_a_conversation() -> None:
    """The control. Keying on the item instead of the schedule would pass the
    test above and merge two unrelated reports into one thread."""
    spec, locator, item_id = _locator_and_item()

    a, _ = locator.chat_for_schedule(item_id, "build-report", "wui:i1:aaa")
    b, _ = locator.chat_for_schedule(item_id, "close-month", "wui:i1:bbb")

    assert a != b
    assert len(list_item_conversations(spec.get_resource_manager(Conversation), item_id)) == 2


def test_a_reused_schedule_chat_survives_a_failed_run() -> None:
    """`settle_run_chat(chat, None)` deletes, which is right for a chat opened
    for THIS run and wrong for one the schedule has been using — deleting it
    would take the schedule's whole history with it because one night's start
    failed."""
    spec, locator, item_id = _locator_and_item()
    chat_id, created = locator.chat_for_schedule(item_id, "build-report", "wui:i1:abc")
    locator.settle_run_chat(chat_id, "run-1")

    again, created_again = locator.chat_for_schedule(item_id, "build-report", "wui:i1:abc")

    assert again == chat_id
    assert created_again is False, "a caller told it created this would then delete it on failure"
    assert list_item_conversations(spec.get_resource_manager(Conversation), item_id)


def test_a_schedules_thread_never_becomes_the_items_default_conversation() -> None:
    """The invariant `chat_for_schedule` states, asserted at last.

    `find_default_conversation` picks the earliest FREE chat — free meaning
    `run_id is None` — and that is what the item opens for a person who just
    clicks in. A schedule's 03:00 thread becoming that means someone opens their
    item and lands in a machine's log, with their own conversation somewhere
    below it. It is P22's headline failure returning through the scheduled door.

    Nothing held this. Changing the created `run_id` from `""` to `None` — which
    is exactly that failure — left 191 chat-and-conversation tests green.

    Asserted through `find_default_conversation` itself rather than by reading
    the field, because the field is not the rule: the comment beside it said
    "non-empty from the start", and `""` is empty. What makes it work is that
    the rule tests `is None`, and only a test that asks the rule can tell the
    difference between a property and a sentence about one.
    """
    spec, locator, item_id = _locator_and_item()
    conv_rm = spec.get_resource_manager(Conversation)

    assert find_default_conversation(conv_rm, item_id) is None, "the item starts with none"

    locator.chat_for_schedule(item_id, "build-report", "wui:i1:abc")

    assert find_default_conversation(conv_rm, item_id) is None, (
        "a schedule's thread became the item's default conversation — a person "
        "opening this item now lands in a machine's log"
    )

    # The control: a chat a PERSON opens is free, and does become the default.
    # Without this the assertion above is satisfied by nothing ever being default.
    human = conv_rm.create(Conversation(item_id=item_id, title="mine", created_ms=1)).resource_id
    found = find_default_conversation(conv_rm, item_id)
    assert found is not None and found[0] == human, "a person's own chat is the default"


def test_deleting_a_schedules_thread_does_not_stop_the_schedule() -> None:
    """A TRADE-OFF, pinned so it cannot change by accident.

    `chat_for_schedule` restores a soft-deleted row rather than minting a new
    one, and that `restore` is load-bearing: without it the key resolves to a
    deleted conversation and `workflow_exec.drive_turn`, which catches
    `ResourceIDNotFoundError` and not `ResourceIsDeletedError`, crashes instead
    of falling back.

    The cost is real and worth stating plainly: a person who deletes a
    schedule's thread gets it back on the next fire, history included. Deleting
    the conversation is not how you stop a schedule — removing its row from
    `schedules.json` is, and that is what `reference.md` tells the author.

    The alternatives are worse. Minting a fresh chat would change the key every
    time somebody tidied up, and the key is what `active_run_for_chat` collides
    on — so the one-run rule would switch off for that schedule. Refusing to
    fire would let a stray click stop a nightly report with nothing to say why.
    """
    spec, locator, item_id = _locator_and_item()
    conv_rm = spec.get_resource_manager(Conversation)

    key = "wui:i1:abc"
    first, created = locator.chat_for_schedule(item_id, "build-report", key)
    assert created is True
    conv_rm.delete(first)

    again, created_again = locator.chat_for_schedule(item_id, "build-report", key)

    assert again == first, "the schedule lost its thread and would key on a new one"
    assert created_again is False, "a restored thread is not this call's to clean up"
    assert len(list_item_conversations(conv_rm, item_id)) == 1, (
        "the delete left a second thread behind"
    )

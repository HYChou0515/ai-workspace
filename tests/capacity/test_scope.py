"""Whose work this is (docs/plan-capacity-usage.md §6.2, §6.3, §7): the usage
scope a turn, a job or a request sets, and the request's identity holder that
attributes without asking `get_user_id` twice and closes when the request ends."""

import asyncio

from workspace_app.capacity.scope import (
    RequestIdentity,
    UsageScope,
    current,
    recording_reader,
    request_identity,
    scoped,
)


def test_no_scope_outside_any_work():
    assert current() is None


def test_a_scope_holds_for_the_work_inside_it_and_ends_with_it():
    with scoped(UsageScope(user="alice", app="pm", item="i1", purpose="chat")):
        assert current() == UsageScope(user="alice", app="pm", item="i1", purpose="chat")
    assert current() is None


def test_a_reader_called_during_a_request_tells_the_request_who_it_is():
    calls = []

    def reader() -> str:
        calls.append(1)
        return "alice"

    wrapped = recording_reader(reader)
    holder = RequestIdentity(app="pm")
    with request_identity(holder):
        assert wrapped() == "alice"
        assert current() == UsageScope(user="alice", app="pm", purpose="request")
    assert holder.user == "alice"
    assert calls == [1]


def test_a_request_nobody_asked_about_reads_as_unknown_until_resolved():
    holder = RequestIdentity(app="")
    with request_identity(holder):
        assert current() == UsageScope(user="unknown", app="", purpose="request")


def test_a_closed_request_neither_attributes_nor_records():
    """A task the request started copies its context, holder included; after
    the request ends it must not attribute later work to that person, and a
    later reader call must not rewrite who the request was."""
    holder = RequestIdentity(app="pm")
    seen: list[UsageScope | None] = []

    async def scenario() -> None:
        with request_identity(holder):
            started = asyncio.Event()

            async def later() -> None:
                await started.wait()
                seen.append(current())
                recording_reader(lambda: "mallory")()

            task = asyncio.create_task(later())
            recording_reader(lambda: "alice")()
        holder.close()
        started.set()
        await task

    asyncio.run(scenario())

    assert seen == [None]
    assert holder.user == "alice"


def test_a_scope_set_inside_a_request_wins_over_the_request():
    holder = RequestIdentity(app="pm")
    with request_identity(holder), scoped(UsageScope(user="bob", purpose="chat")):
        assert current() == UsageScope(user="bob", purpose="chat")

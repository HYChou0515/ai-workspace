"""A failed run also tells the person it ran as (`docs/plan-personal-env.md`, D11).

A run started by a page button, the workflow panel's Run, or a schedule bound
with "run as me" uses THAT person's credentials. When it fails, the item's owner
is told (as before) — and so is the person it ran as, who is the one who can
sign in again. The platform cannot tell an expired token from any other
failure, so the notice says what to do IF it was the sign-in, and links there.
One per person, item, workflow and day: a schedule that fails every hour must
not bury them.
"""

from __future__ import annotations

from workspace_app.resources import Notification
from workspace_app.workflow.run import RunStatus, WorkflowRun
from workspace_app.workflow.run_identity import RunIdentities

from .test_headless_env import _executor_app


def _failed(item_id: str, workflow_id: str = "nightly") -> WorkflowRun:
    return WorkflowRun(
        item_id=item_id,
        captured_user="owner-o",
        workflow_id=workflow_id,
        status=RunStatus.ERROR,
        current_phase="fetch",
    )


def _run_as(spec, run_id: str, item_id: str, who: str, workflow_id: str = "nightly") -> None:
    RunIdentities(spec).record(
        run_id,
        who,
        verb="execute",
        item_id=item_id,
        profile="echo",
        workflow_id=workflow_id,
        workflow_digest="d",
    )


def _inbox(spec) -> list[tuple[str, str]]:
    rm = spec.get_resource_manager(Notification)
    out = []
    for r in rm.list_resources():
        n = r.data
        assert isinstance(n, Notification)
        out.append((n.recipient, n.link))
    return sorted(out)


def test_the_person_a_failed_run_ran_as_is_told_where_to_sign_in_again():
    executor, item_id, spec, _runner, _client = _executor_app(None, owner="owner-o")
    _run_as(spec, "run-1", item_id, "bob")

    executor.notify_failure(_failed(item_id), "run-1")

    assert _inbox(spec) == [
        ("bob", "/my-env"),
        ("owner-o", f"/a/playground/items/{item_id}"),
    ]


def test_the_owner_running_their_own_workflow_is_told_once_with_the_hint():
    """One notice, not two — but it still says where to sign in again: the owner
    of a bound schedule is the person most likely to need it (round 1)."""
    executor, item_id, spec, _runner, _client = _executor_app(None, owner="owner-o")
    _run_as(spec, "run-1", item_id, "owner-o")

    executor.notify_failure(_failed(item_id), "run-1")

    rm = spec.get_resource_manager(Notification)
    (only,) = [r.data for r in rm.list_resources()]
    assert isinstance(only, Notification)
    assert only.recipient == "owner-o"
    assert "My environment variables" in only.body


def test_the_owner_is_not_told_to_sign_in_again_for_someone_else():
    """Bob's run failed: only Bob can renew Bob's sign-in, so the hint goes to
    him — on the owner's notice it would send them to a page that fixes nothing."""
    executor, item_id, spec, _runner, _client = _executor_app(None, owner="owner-o")
    _run_as(spec, "run-1", item_id, "bob")

    executor.notify_failure(_failed(item_id), "run-1")

    rm = spec.get_resource_manager(Notification)
    bodies = {r.data.recipient: r.data.body for r in rm.list_resources()}
    assert "My environment variables" in bodies["bob"]
    assert bodies["owner-o"] == ""


def test_a_run_nobody_ran_as_tells_only_the_owner():
    """An unbound schedule or an entity trigger used nobody's credentials."""
    executor, item_id, spec, _runner, _client = _executor_app(None, owner="owner-o")

    executor.notify_failure(_failed(item_id), "run-1")

    assert [who for who, _ in _inbox(spec)] == ["owner-o"]


def test_someone_removed_from_the_item_is_not_told(monkeypatch):
    """Their values were not lent (the same gate refused them), and the notice
    would name a phase of an item they can no longer open (round 1, N2)."""
    from workspace_app.api.private_env import PrivateEnvStore

    executor, item_id, spec, _runner, _client = _executor_app(None, owner="owner-o")
    monkeypatch.setattr(
        executor, "_private_env", PrivateEnvStore(spec, may=lambda u, _i, _v: u != "bob")
    )
    _run_as(spec, "run-1", item_id, "bob")

    executor.notify_failure(_failed(item_id), "run-1")

    assert [who for who, _ in _inbox(spec)] == ["owner-o"]


def test_the_same_failure_all_day_is_one_notice(monkeypatch):
    import workspace_app.api.workflow_exec as wx

    day = 1_760_000_000_000  # some UTC day
    executor, item_id, spec, _runner, _client = _executor_app(None, owner="owner-o")
    for i in range(3):
        monkeypatch.setattr(wx, "now_ms", lambda i=i: day + i * 3_600_000)
        _run_as(spec, f"run-{i}", item_id, "bob")
        executor.notify_failure(_failed(item_id), f"run-{i}")
    monkeypatch.setattr(wx, "now_ms", lambda: day + 86_400_000)
    _run_as(spec, "run-next-day", item_id, "bob")
    executor.notify_failure(_failed(item_id), "run-next-day")
    _run_as(spec, "run-other", item_id, "bob", workflow_id="weekly")
    executor.notify_failure(_failed(item_id, "weekly"), "run-other")

    bob = [link for who, link in _inbox(spec) if who == "bob"]
    assert len(bob) == 3  # day one, the next day, and another workflow

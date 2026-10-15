"""sandbox-host samples its own sandboxes' cgroups (docs/plan-capacity-usage.md
§6.5): every live sandbox is a target named by its item, and by the person and
App the app labelled it with at create; the sampler runs on its own timer and
writes what it has when the host stops."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.__main__ import _usage_task
from sandbox_host.app import make_host_app
from sandbox_host.cgroup_usage import Target
from sandbox_host.config import load_settings
from sandbox_host.mock import MockSandbox


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h")


async def test_a_labelled_sandbox_is_a_usage_target_named_by_item_person_and_app():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        rid = (
            await c.post(
                "/sandboxes", json={"item_id": "i1", "labels": {"user": "alice", "app": "pm"}}
            )
        ).json()["remote_id"]

    assert app.state.controller.usage_targets() == [
        Target(sandbox=rid, item="i1", user="alice", app="pm")
    ]


async def test_an_older_app_sending_no_labels_still_gets_its_sandbox_measured():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i1"})).json()["remote_id"]

    assert app.state.controller.usage_targets() == [Target(sandbox=rid, item="i1", user="", app="")]


async def test_a_killed_sandbox_stops_being_a_target():
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        rid = (await c.post("/sandboxes", json={"item_id": "i1"})).json()["remote_id"]
        assert (await c.delete(f"/sandboxes/{rid}")).status_code == 204

    assert app.state.controller.usage_targets() == []


def test_the_sampling_knobs_come_from_the_environment():
    s = load_settings({"SANDBOX_HOST_USAGE_SAMPLE_S": "30", "SANDBOX_HOST_USAGE_WINDOW_S": "60"})

    assert (s.usage_sample_s, s.usage_window_s) == (30.0, 60)
    assert (load_settings({}).usage_sample_s, load_settings({}).usage_window_s) == (60.0, 300)


def test_a_window_that_does_not_tile_the_hour_is_refused_with_its_name():
    with pytest.raises(ValueError, match="SANDBOX_HOST_USAGE_WINDOW_S"):
        load_settings({"SANDBOX_HOST_USAGE_WINDOW_S": "7"})


class _Sampler:
    def __init__(self) -> None:
        self.samples = 0
        self.closed = False

    def sample(self) -> None:
        self.samples += 1

    def close(self) -> None:
        self.closed = True


def test_no_sampling_when_switched_off():
    assert _usage_task(_Sampler(), sample_s=0) is None


async def test_the_sampler_runs_on_its_timer_and_writes_the_rest_when_stopped():
    sampler = _Sampler()
    task = _usage_task(sampler, sample_s=0.02)
    assert task is not None
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sampler.samples >= 2
    assert sampler.closed


async def test_listing_targets_survives_a_sandbox_made_while_it_runs():
    """The sampler lists the targets in a thread while the event loop creates
    and kills sandboxes. A create landing mid-list must not end the sample
    with "dictionary changed size during iteration"."""
    app = make_host_app(MockSandbox(), advertise_url="http://h")
    async with _client(app) as c:
        await c.post("/sandboxes", json={"item_id": "i1"})
        await c.post("/sandboxes", json={"item_id": "i2"})
    controller = app.state.controller

    class _CreatesOnLookup(dict):
        def get(self, key, default=None):  # a create arrives while the list is walked
            controller._last_active[f"new-{len(controller._last_active)}"] = 0.0
            return super().get(key, default)

    controller._labels_of = _CreatesOnLookup(controller._labels_of)

    assert len(controller.usage_targets()) == 2

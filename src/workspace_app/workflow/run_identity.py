"""Whose PRIVATE env values a run's tools get (`docs/plan-wui-viewer-login.md`).

The person who pressed the button that started it — a page's ``wui/run``, the
workflow panel's Run — or the binder of a schedule. NOT ``WorkflowRun.
captured_user``, which is who the run is attributed and billed to (the owner,
for a page's run).

Its own model rather than a field on ``WorkflowRun`` (review round 1, R5):
``WorkflowRun`` has auto-CRUD with no write gate, so a field there let anyone
PATCH a paused run into someone else's private values. This is registered
after ``spec.apply``, so no route reaches it at all. No row ⇒ nobody's values.
"""

from __future__ import annotations

import contextlib

from msgspec import Struct
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError


class RunIdentity(Struct):
    run_id: str
    env_user: str


def register_run_identity(spec: SpecStar) -> None:
    with contextlib.suppress(ValueError):
        spec.add_model(RunIdentity)


class RunIdentities:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _rm(self):
        return self._spec.get_resource_manager(RunIdentity)

    def record(self, run_id: str, env_user: str) -> None:
        self._rm().create(RunIdentity(run_id=run_id, env_user=env_user), resource_id=run_id)

    def env_user(self, run_id: str) -> str:
        try:
            data = self._rm().get(run_id).data
        except (ResourceIDNotFoundError, KeyError):
            return ""
        assert isinstance(data, RunIdentity)
        return data.env_user

    def forget(self, run_id: str) -> None:
        with contextlib.suppress(ResourceIDNotFoundError, KeyError):
            self._rm().permanently_delete(run_id)

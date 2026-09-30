"""Whose PRIVATE env values a run's tools get (`docs/plan-wui-viewer-login.md`).

The person who pressed the button that started it — a page's ``wui/run``, the
workflow panel's Run — or the binder of a schedule. NOT ``WorkflowRun.
captured_user``, which is who the run is attributed and billed to (the owner,
for a page's run).

Its own model rather than a field on ``WorkflowRun`` (review round 1, R5):
``WorkflowRun`` has auto-CRUD with no write gate, so a field there let anyone
PATCH a paused run into someone else's private values. This is registered
after ``spec.apply``, so no route reaches it at all. No row ⇒ nobody's values.

Forgotten when the orchestrator prunes the run and when the item is deleted.
A run row deleted through its auto-CRUD leaves this row behind (round 2, R6):
harmless — it holds a name, no values, and a run id nothing will start again.
"""

from __future__ import annotations

import contextlib

from msgspec import Struct
from specstar import SpecStar
from specstar.types import ResourceIDNotFoundError


class RunIdentity(Struct):
    run_id: str
    env_user: str
    #: The right that admitted them to start it — what they must STILL hold
    #: when their values are used (`execute` for a page's run, `converse` for
    #: the workflow panel's Run). Round 2, D2.
    verb: str = "execute"
    #: The workflow the run STARTED with, and the digest of its file's bytes
    #: (`schedule_bindings.workflow_digest` — the same bytes a schedule binding
    #: consents to; "" for a profile's workflow, which ships with the deploy).
    #: Consent is to that file: checked where the values are USED (each agent
    #: node, `workflow_exec`), so a PATCHed `workflow_id`, a file edited before
    #: a resume or a gate decision, or one edited mid-run all run as nobody.
    #: Round 2 D1, round 3 defect 1 (a manifest digest missed the steps).
    workflow_id: str = ""
    workflow_digest: str = ""


def register_run_identity(spec: SpecStar) -> None:
    with contextlib.suppress(ValueError):
        spec.add_model(RunIdentity)


class RunIdentities:
    def __init__(self, spec: SpecStar) -> None:
        self._spec = spec

    def _rm(self):
        return self._spec.get_resource_manager(RunIdentity)

    def record(
        self, run_id: str, env_user: str, *, verb: str, workflow_id: str, workflow_digest: str
    ) -> None:
        self._rm().create(
            RunIdentity(
                run_id=run_id,
                env_user=env_user,
                verb=verb,
                workflow_id=workflow_id,
                workflow_digest=workflow_digest,
            ),
            resource_id=run_id,
        )

    def get(self, run_id: str) -> RunIdentity | None:
        try:
            data = self._rm().get(run_id).data
        except (ResourceIDNotFoundError, KeyError):
            return None
        assert isinstance(data, RunIdentity)
        return data

    def env_user(self, run_id: str) -> str:
        found = self.get(run_id)
        return found.env_user if found is not None else ""

    def forget(self, run_id: str) -> None:
        with contextlib.suppress(ResourceIDNotFoundError, KeyError):
            self._rm().permanently_delete(run_id)

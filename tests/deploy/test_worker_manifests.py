"""Every JobType a worker can drain has somewhere to drain it.

`_JOBTYPE_ATTR` is what `python -m workspace_app.worker <jobtype>` accepts; the
manifests are what actually runs one. A JobType present in the first and absent
from the second is invisible under the documented pod-split shape
(`server.run_consumers: false`, API as pure producer): the queue fills, nothing
drains it, and to the caller that is indistinguishable from a queue that never
moves. #715 shipped that way — the coordinator reached the bundle, the routes,
`create_app` and the JobType table, and no Deployment.

Derived, not enumerated. A list of names here would have the same blind spot as
the thing it guards: the new entry is exactly the one nobody remembers to add.
"""

from __future__ import annotations

import pathlib

import yaml

from workspace_app.worker import _JOBTYPE_ATTR

_MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "kubernetes" / "base" / "workers.yaml"


def _worker_commands() -> dict[str, str]:
    """{jobtype -> deployment name} for every worker Deployment in the manifest."""
    out: dict[str, str] = {}
    for doc in yaml.safe_load_all(_MANIFEST.read_text()):
        if not doc or doc.get("kind") != "Deployment":
            continue
        name = doc["metadata"]["name"]
        for container in doc["spec"]["template"]["spec"]["containers"]:
            cmd = container.get("command") or []
            # ["python", "-m", "workspace_app.worker", "<jobtype>"]
            if len(cmd) >= 4 and cmd[1:3] == ["-m", "workspace_app.worker"]:
                out[cmd[3]] = name
    return out


def test_every_jobtype_has_a_worker_deployment():
    missing = sorted(set(_JOBTYPE_ATTR) - set(_worker_commands()))
    assert not missing, (
        f"JobTypes with no worker Deployment in {_MANIFEST.name}: {missing}. "
        "A pod-split deploy accepts their work and never does it."
    )


def test_no_worker_deployment_drains_a_jobtype_that_does_not_exist():
    """The other direction: a Deployment naming a jobtype the CLI rejects
    crashloops on boot, which is at least loud — but it is still a manifest that
    can never work, and a rename is how it happens."""
    unknown = sorted(set(_worker_commands()) - set(_JOBTYPE_ATTR))
    assert not unknown, f"worker Deployments for unknown JobTypes: {unknown}"

"""The arguments of each command the model is offered.

Which commands exist is the contract's (`JobBackend.commands`): seven with
`capacity`, six without (no `queue`). What `run` takes is half the
backend's: the resources are its `resources_schema`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_contract import JobBackend

#: How many characters of log one `logs` call returns unless asked otherwise.
#: Tool output is capped by the platform; a page this size stays well inside it.
LOG_PAGE = 4000
#: How many jobs one `jobs` call shows unless asked otherwise, and at most.
JOBS_PAGE = 20
JOBS_MAX = 100


@dataclass(frozen=True)
class Command:
    name: str
    params_json_schema: dict[str, Any]


_JOB_ID = {"type": "string", "description": "The job's id, as run or jobs gave it."}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _params(name: str, backend: type[JobBackend]) -> dict[str, Any]:
    if name == "run":
        return _object(
            {
                "workdir": {
                    "type": "string",
                    "description": "The folder to send, as a path in the workspace.",
                },
                "resources": backend.resources_schema,
            },
            ["workdir", "resources"],
        )
    if name == "jobs":
        return _object(
            {"limit": {"type": "integer", "minimum": 1, "maximum": JOBS_MAX, "default": JOBS_PAGE}},
            [],
        )
    if name == "queue":
        return _object({}, [])
    if name == "files":
        return _object(
            {
                "job_id": _JOB_ID,
                "path": {
                    "type": "string",
                    "default": ".",
                    "description": "A folder in the job's folder; leave out for all of it.",
                },
            },
            ["job_id"],
        )
    if name == "fetch":
        return _object(
            {
                "job_id": _JOB_ID,
                "path": {
                    "type": "string",
                    "description": "A file or folder in the job's folder, as files lists it.",
                },
                "dest": {
                    "type": "string",
                    "description": "Where to write it in the workspace. Leave out for "
                    "jobs/<job_id>/fetched/<path>.",
                },
            },
            ["job_id", "path"],
        )
    if name == "logs":
        return _object(
            {
                "job_id": _JOB_ID,
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Where to start reading; leave out for the end of the log.",
                },
                "limit": {"type": "integer", "minimum": 1, "default": LOG_PAGE},
            },
            ["job_id"],
        )
    # cancel
    return _object({"job_id": _JOB_ID}, ["job_id"])


def commands(backend: type[JobBackend]) -> dict[str, Command]:
    """The commands to offer for `backend`, in a fixed order."""
    return {name: Command(name, _params(name, backend)) for name in backend.commands()}

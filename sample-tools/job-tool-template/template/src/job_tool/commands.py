"""The commands a backend is offered to the model as.

A command exists for each call the backend supports (`JobBackend.offers`),
except the two the shell keeps to itself: `caller_id` (whose key this is) and
`find_by_key` (did a submit that ran out of time land?).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_contract import JobBackend

#: Calls the shell makes on its own and never offers.
INTERNAL = ("caller_id", "find_by_key")


@dataclass(frozen=True)
class Command:
    name: str
    params_json_schema: dict[str, Any]


#: How many characters of log one `logs` call returns unless asked otherwise.
#: Tool output is capped by the platform; a page this size stays well inside it.
LOG_PAGE = 4000
#: How many jobs one `list` call returns unless asked otherwise, and at most.
#: Tool output is capped by the platform, so an unbounded page would come back
#: cut off — and a cut-off list reads as "these are all my jobs".
LIST_PAGE = 20
LIST_MAX = 100

_JOB_ID = {"type": "string", "description": "The job's id, as `submit` or `list` returned it."}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


def _params(name: str, backend: type[JobBackend]) -> dict[str, Any]:
    contract = backend.contract
    if name == "submit":
        # The author's spec IS the argument: no wrapper for the model to get wrong.
        return contract.spec_schema
    if name == "list":
        return _object(
            {
                "cursor": {
                    "type": ["string", "null"],
                    "description": "Where the previous page ended; leave out for the newest jobs.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": LIST_MAX,
                    "default": LIST_PAGE,
                },
            },
            [],
        )
    if name == "update":
        assert contract.patch_schema is not None
        return _object({"job_id": _JOB_ID, "patch": contract.patch_schema}, ["job_id", "patch"])
    if name == "logs":
        return _object(
            {
                "job_id": _JOB_ID,
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "default": LOG_PAGE},
            },
            ["job_id"],
        )
    if name == "fetch":
        return _object(
            {
                "job_id": _JOB_ID,
                "name": {"type": "string", "description": "Which output to fetch."},
                "dest": {
                    "type": "string",
                    "description": "Where to write it, relative to the workspace.",
                },
            },
            ["job_id", "name", "dest"],
        )
    # get, cancel
    return _object({"job_id": _JOB_ID}, ["job_id"])


def commands(backend: type[JobBackend]) -> dict[str, Command]:
    """The commands to offer for `backend`, in a fixed order."""
    offered = [name for name in backend.offers() if name not in INTERNAL]
    return {name: Command(name=name, params_json_schema=_params(name, backend)) for name in offered}

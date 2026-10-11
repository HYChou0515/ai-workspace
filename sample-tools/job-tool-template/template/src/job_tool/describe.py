"""What the model reads about each command."""

from __future__ import annotations

from job_contract import JobBackend

_WHAT = {
    "submit": "Submit a job.",
    "get": "Show one job's status.",
    "list": "List your jobs, newest first.",
    "update": "Change a job that was already submitted.",
    "cancel": "Cancel a job.",
    "logs": "Read part of a job's log.",
    "fetch": "Save one of a job's outputs into the workspace.",
}


def describe(name: str, backend: type[JobBackend]) -> str:
    return _WHAT[name]

"""What the model reads about each command.

Two halves. What the jobs ARE is the author's (`Contract.domain_description`,
and the descriptions inside their schemas). How a job BEHAVES — returns at
once, unknown is not gone, accepted is not applied, nothing calls back — is the
same for every backend and is written here, from the contract, so no author
restates it and no restatement drifts.
"""

from __future__ import annotations

from job_contract import JobBackend


def _submit(backend: type[JobBackend]) -> str:
    rule = backend.contract.idempotency
    who = "anyone" if not rule.per_user else "you"
    return (
        "Submit a job. Returns at once with its job_id; the job then runs on its own and "
        "nothing announces when it ends, so tell the user to ask about it later. "
        f"If {who} already submitted the same {', '.join(rule.fields)}, this returns that "
        "job instead of starting another."
    )


_FIXED = {
    "get": (
        "Show one job. Its phase is running, done or unknown; unknown means the backend "
        "could not say just now and does NOT mean the job does not exist. Its state is the "
        "backend's own word for where the job is."
    ),
    "list": "List your jobs, newest first, one page at a time.",
    "update": (
        "Change a job that was already submitted; the patch says what can change. The "
        "change is accepted, not necessarily applied: call get to see when it has taken "
        "effect."
    ),
    "cancel": "Cancel a job. Cancelling a job that already finished is not an error.",
    "logs": (
        "Read part of a job's log. The answer says the log's total length and where to "
        "continue reading."
    ),
    "fetch": "Save one of a job's outputs to a path in the workspace.",
}


def describe(name: str, backend: type[JobBackend]) -> str:
    if name != "submit":
        return _FIXED[name]
    domain = backend.contract.domain_description.strip()
    return f"{domain} {_submit(backend)}" if domain else _submit(backend)

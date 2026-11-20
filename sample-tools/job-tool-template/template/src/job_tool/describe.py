"""What the model reads about each command.

`run` is two halves. How to lay out a folder for this backend, and what its
machines have, is the backend's (`run_description`, D4). How the tool behaves
— what is not sent, that a large folder takes several calls, what counts as
the same job, that nothing calls back — is the same for every backend and is
written here, so no author restates it and no restatement drifts. The other
commands are the tool's words unless the backend gives its own.
"""

from __future__ import annotations

from job_contract import JobBackend

_RUN = (
    "run sends the folder `workdir` of the workspace to that machine and starts it with "
    "`resources`. Not sent: folders named .git, .venv, venv, node_modules, __pycache__ and "
    "a few other caches; .pyc files; links; and what the folder's own top-level .gitignore "
    "names. A large folder takes "
    "several calls: the answer says how much has been sent, so call run again with the "
    "same arguments until it says started. The same folder content with the same "
    "resources, while that job is unfinished, is that job — not a second one — until it "
    "finishes; after that, the same request starts a new one. run does not wait for the "
    "job to run, and nothing announces when it ends: tell the user to ask about it later, "
    "then call jobs."
)
_QUEUE_HINT = " Unsure what to ask for? Call queue to see what is free now."

_DEFAULTS = {
    "jobs": (
        "The jobs sent from this workspace, newest first (20 unless limit says more), each "
        "with where it is now: "
        "queued (with its place in the queue when known), running, succeeded, failed, or "
        "unknown — the backend could not say just now, which does NOT mean the job is gone. "
        "A job the backend no longer has is failed. A job not fully sent yet says how to "
        "finish sending it."
    ),
    "queue": (
        "The machines (idle, busy, what each has free), the jobs waiting for one, and the "
        "quota left, as far as the backend shows them."
    ),
    "files": (
        "List what a job has in its folder on the other machine — what it was sent and "
        "what it wrote — with sizes. Works while it runs, too."
    ),
    "fetch": (
        "Copy a file or a folder from a job's folder on the other machine into the "
        "workspace. A large one takes several calls: call fetch again with the same "
        "arguments until it says done."
    ),
    "logs": (
        "Read a job's log. Without an offset, the end of it; the answer says the log's "
        "total length and where to read on."
    ),
    "cancel": "Cancel a job. Cancelling a job that already finished is not an error.",
}


def describe(name: str, backend: type[JobBackend]) -> str:
    if name == "run":
        hint = _QUEUE_HINT if "queue" in backend.commands() else ""
        return f"{backend.run_description.strip()}\n\n{_RUN}{hint}"
    return backend.descriptions.get(name, _DEFAULTS[name])

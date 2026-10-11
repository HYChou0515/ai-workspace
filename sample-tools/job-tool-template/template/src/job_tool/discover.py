"""Finding the backend this tool talks to.

The backend is its own package. It registers its `JobBackend` subclass under
one entry point group, and this tool loads whatever is registered there — so
replacing the backend is a dependency change, never an edit to this repository.

In the backend's pyproject.toml:

    [project.entry-points."job_tool.backend"]
    acme = "acme_jobs.backend:AcmeBackend"
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points

from job_contract import JobBackend

GROUP = "job_tool.backend"


class NoBackend(Exception):
    """Not exactly one usable backend is installed. The tool cannot start."""


def load_backend(found: Iterable[EntryPoint] | None = None) -> type[JobBackend]:
    """The one backend registered under `GROUP`."""
    registered = list(entry_points(group=GROUP) if found is None else found)
    if not registered:
        raise NoBackend(
            f"no backend is installed: register your JobBackend subclass under the "
            f"entry point group {GROUP!r} in your backend package"
        )
    if len(registered) > 1:
        names = ", ".join(f"{ep.name} ({ep.value})" for ep in registered)
        raise NoBackend(f"more than one backend is installed ({names}); install exactly one")
    (ep,) = registered
    backend = ep.load()
    if not (isinstance(backend, type) and issubclass(backend, JobBackend)):
        raise NoBackend(f"{ep.value} is not a JobBackend subclass")
    return backend

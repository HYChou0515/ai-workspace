"""#186 helper: keep a specstar job queue's lifecycle writes credited to the
job's *creator* instead of the worker pod's default acting-user."""

from __future__ import annotations

from typing import TYPE_CHECKING

from specstar.resource_manager.basic import Ctx

if TYPE_CHECKING:
    from specstar.types import IResourceManager


def preserve_job_creator(job_rm: IResourceManager) -> None:
    """Drop a job ``ResourceManager``'s default acting-user so specstar's own
    lifecycle status writes (claim ``PROCESSING`` / ``complete`` ``COMPLETED``,
    which run in the consumer thread, *outside* our handler) fall through to the
    job's persisted ``created_by`` instead of the worker's bare default.

    specstar credits every lifecycle write via ``_rm_using(created_by)``, which
    resolves the acting user as ``user_or_unset or created_by``. When the manager
    carries a default user — our app threads ``get_user_id`` as the *spec-wide*
    default, and ``add_model`` propagates it to every model including the Job
    models — ``user_or_unset`` is that default and shadows ``created_by``. A job
    pod has no request, so the default is the bare ``server.default_user`` and the
    worker rewrites the job's audit to it: the root of #186 ("index-job's updater
    is still anonymous"). With no default, ``user_or_unset`` is ``UNSET`` and the
    creator is preserved through the whole lifecycle.

    The trade-off: the manager now has NO fallback user, so EVERY enqueue must set
    it explicitly via ``job_rm.using(user=...)`` (a bare ``create`` would raise).
    The producers do exactly that — the requester in a request, the run's
    requester (``job.info.created_by``, now preserved) in the worker fan-out.

    Only the manager's programmatic write context is changed; the HTTP route
    ``DependencyProvider`` keeps the spec default, so auto-CRUD reads are
    unaffected.
    """
    # `user_ctx` is a concrete-ResourceManager attribute (not on the interface).
    job_rm.user_ctx = Ctx("user_ctx", strict_type=str)  # ty: ignore[unresolved-attribute]

"""Durable wiki-maintenance queue types (#58/#59).

The wiki maintainer used to run off an in-process asyncio queue — fine for
one process, but invisible to a second pod and lost on restart. These types
move the work onto a specstar job queue:

  - ``WikiJobPayload`` — one source to fold into one collection's wiki.
  - ``WikiMaintenanceJob`` — the ``specstar.Job`` carrying that payload.
    ``partition_key`` = the collection id, so specstar serialises a
    collection's maintenance across *every* consumer (per-collection serial,
    cross-pod — the framework's guarantee, not ours). One job per source
    keeps the Karpathy "integrate one source per pass" shape.

The job is enqueued with ``rm.create(...)`` and consumed by
``rm.start_consume(block=False)``; the handler (wired in the coordinator,
which owns the runtime deps) runs ``run_wiki_maintainer`` for that one source.
"""

from __future__ import annotations

import msgspec
from specstar.types import Job


class WikiJobPayload(msgspec.Struct):
    """One unit of wiki maintenance. ``op`` selects the pass:

    - ``fold`` (default): integrate the SourceDoc ``doc_id`` into the wiki.
      ``doc_id`` is the EXACT source (its natural-key id) — content is resolved
      by id at run time, never by a path scan, so two users' same-path docs each
      fold their own content. ``source_path`` is kept for debugging.
    - ``unfold``: a source was DELETED; scrub its traces from the wiki. The doc
      row is gone by run time, so the removed source's display label
      (``removed_label``) + extracted text (``removed_text``) are SNAPSHOTTED
      here at enqueue time for the remove-pass to grep + scrub."""

    collection_id: str
    source_path: str
    doc_id: str = ""
    # fold | unfold | code_sync | code_split | code_card | code_finalize.
    # #355: code_sync clones the collection's git_url + ingests it (off the API,
    # on the wiki worker), then chains to code_split — the head of the build.
    op: str = "fold"
    removed_label: str = ""
    removed_text: str = ""
    # #281 P4 code-wiki fan-out: a ``code_card`` job builds the cards for ONE
    # batch (``batch_paths``) and records ``batch_index`` in the CodeWikiBuildRun
    # CAS join. ``code_split`` / ``code_finalize`` carry neither.
    batch_index: int = 0
    batch_paths: list[str] = msgspec.field(default_factory=list)


class WikiMaintenanceJob(Job[WikiJobPayload]):
    """A queued wiki-maintenance run. ``partition_key`` is set to the
    collection id at enqueue time so a collection's jobs run serially across
    consumers; ``status`` drives the live build progress (see
    ``WikiBuildState``)."""

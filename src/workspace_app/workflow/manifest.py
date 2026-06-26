"""``WorkflowManifest`` — the declarative part of a profile-level workflow (#100,
manual §3 & §14).

A profile carries its workflows by declaring a ``workflows: [...]`` list in its
``_profile.json`` (manual §4) — each entry a ``WorkflowManifest`` with a stable
``id`` whose ``run.py`` lives at ``profiles/<name>/workflows/<id>/run.py``. The
legacy singular ``workflow`` block (one workflow, ``run.py`` at the profile root)
is still accepted and normalised to a one-element list. A profile's *having* a
workflow is what makes it headless-triggerable; each manifest's content is just the
phase skeleton (for the read-only progress diagram, manual §12) and where the run's
``input.json`` lives — the only thing the platform knows about inputs (manual §14).
The orchestration itself is the workflow's ``run.py`` (code), not data.
"""

from __future__ import annotations

from typing import Any

from msgspec import Struct, field


class WorkflowPhase(Struct):
    """A phase node in the read-only progress diagram (manual §12)."""

    id: str
    title: str = ""


class WorkflowManifest(Struct):
    """A profile's workflow declaration (``_profile.json`` → ``workflow``)."""

    id: str = ""
    """Stable identifier within its profile (manual §4). In the new ``workflows: [...]``
    list form every entry carries a non-empty, unique id and its ``run.py`` lives at
    ``profiles/<name>/workflows/<id>/run.py``. The legacy singular ``workflow`` block
    leaves it ``""`` — the sentinel for the profile-root ``run.py`` layout."""
    title: str = ""
    phases: list[WorkflowPhase] = field(default_factory=list)
    input_json: str = ""
    """Where the run's ``input.json`` lives — the platform surfaces it to ``run()``;
    its content + the file layout are the profile's business (manual §14). Empty (#198)
    ⇒ derive ``{profile.upload_dir}/input.json`` at run time, so the control file sits
    in the same staging folder a chat attach lands in (default ``uploads/input.json``).
    Set it explicitly only to pin a different location."""
    config: dict[str, Any] = field(default_factory=dict)
    """Profile-level config surfaced to ``run()`` as ``wf.config`` — pre-defined,
    not per-run (manual §20 reads ``wf.config["collections"]``)."""
    description: str = ""
    """One-line human description for the launcher card (FE Run-workflow picker)."""
    tag: str = ""
    """A short kind pill for the launcher card, e.g. ``"batch"`` | ``"single"``."""
    hint: str = ""
    """One-line inputs hint shown under the card (e.g. where to drop files)."""

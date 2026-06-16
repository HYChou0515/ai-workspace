"""``WorkflowManifest`` — the declarative part of a profile-level workflow (#100,
manual §3 & §14).

A profile carries a workflow by declaring a ``workflow`` block in its
``_profile.json``. The block's *presence* is what makes a profile
headless-triggerable; its content is just the phase skeleton (for the read-only
progress diagram, manual §12) and where the run's ``input.json`` lives — the only
thing the platform knows about inputs (manual §14). The orchestration itself is the
profile's ``run.py`` (code), not data.
"""

from __future__ import annotations

from msgspec import Struct, field


class WorkflowPhase(Struct):
    """A phase node in the read-only progress diagram (manual §12)."""

    id: str
    title: str = ""


class WorkflowManifest(Struct):
    """A profile's workflow declaration (``_profile.json`` → ``workflow``)."""

    title: str = ""
    phases: list[WorkflowPhase] = field(default_factory=list)
    input_json: str = "inputs/input.json"
    """Where the run's ``input.json`` lives — the platform surfaces it to ``run()``;
    its content + the file layout are the profile's business (manual §14)."""

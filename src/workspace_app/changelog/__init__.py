"""#441: generate the platform CHANGELOG at PR granularity from git history.

:mod:`.render` holds the pure, testable rendering logic; the ``__main__`` CLI
(`python -m workspace_app.changelog`) walks ``git log --first-parent`` and writes
the packaged ``help_content/CHANGELOG.md``. Driven by the Makefile
(`make changelog-preview`, `make release`); see docs/releasing.md.
"""

from __future__ import annotations

from workspace_app.changelog.render import (
    Commit,
    entries_for,
    parse_git_log,
    render_changelog,
    render_release,
    render_unreleased,
    version_to_date,
)

__all__ = [
    "Commit",
    "entries_for",
    "parse_git_log",
    "render_changelog",
    "render_release",
    "render_unreleased",
    "version_to_date",
]

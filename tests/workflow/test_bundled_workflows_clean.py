"""CI gate (#287) — every bundled App's workflows must be authoring-clean. A
phase typo / drift, a renamed-away run.py, or a duplicate id in a shipped App fails
here, in the normal test loop, instead of only at someone's next boot."""

from __future__ import annotations

import pytest

from workspace_app.apps.catalog import discover_app_slugs
from workspace_app.workflow.authoring import check_app


@pytest.mark.parametrize("slug", discover_app_slugs())
def test_bundled_app_workflows_are_diagnostic_clean(slug):
    diags = check_app(slug)
    assert diags == [], "\n".join(d.render() for d in diags)

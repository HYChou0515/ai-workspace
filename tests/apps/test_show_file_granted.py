"""Every bundled App that can read workspace files can also SHOW one.

Found by running a real turn against a real model, with every unit test green:
`show_file` was in `_WORKSPACE_TOOLS`, so `build_tools(None)` handed it out and
the unit tests agreed — but each `app.json` lists `agent.tools` explicitly, and
that list is the ceiling a real turn resolves. The tool reached nobody. Asked to
plot a chart, the agent did what it did before the tool existed: looked at the
png with `read_image` and typed the path into its answer.

Same shape as #613's live-probe regression, so the guard is a manifest-level
invariant rather than another `build_tools` assertion: an App whose agent may
read a file is an App whose agent may put one in front of the user, and the two
grants have to travel together.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

APPS_DIR = Path(__file__).resolve().parents[2] / "src" / "workspace_app" / "apps"
MANIFESTS = sorted(APPS_DIR.glob("*/app.json"))


def _tools(manifest: Path) -> list[str]:
    agent = json.loads(manifest.read_text()).get("agent") or {}
    return list(agent.get("tools") or [])


def test_there_are_bundled_manifests_to_check():
    """Guard the guard: a glob that silently matches nothing would make every
    parametrised case below vacuously pass."""
    assert MANIFESTS, f"no app.json found under {APPS_DIR}"


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.name)
def test_an_app_that_reads_files_can_also_show_them(manifest: Path):
    tools = _tools(manifest)
    if "read_file" not in tools:
        pytest.skip("no file access — nothing to show")
    assert "show_file" in tools, (
        f"{manifest.parent.name}/app.json grants read_file but not show_file, so its "
        "agent can open a file it produced and still has no way to put it in front "
        "of the user — it will fall back to naming the path in prose."
    )

"""The WUI skill's shipped examples.

A skill's examples are the part an agent COPIES, so they are the part that has
to be complete and consistent — a reference that describes an API is checked by
reading, an example is checked by whether it would have run. Pinned here for the
reason this branch learned twice: a behaviour whose only proof is the code gets
deleted by the next change with nothing turning red.
"""

from __future__ import annotations

import re

import pytest

from workspace_app.apps.shared_skills import SHARED_SKILLS
from workspace_app.apps.skill_payload import skill_payload

EXAMPLES = ("dashboard", "editor")

#: What `dispatchWuiRequest` answers to. An example calling anything else would
#: be teaching the agent an API that does not exist — the one mistake a copied
#: example makes unrecoverable, because it looks authoritative.
VERBS = {
    "listFiles",
    "readFile",
    "writeFile",
    "deleteFile",
    "openFile",
    "whoami",
    "callTool",
    "onFileChanged",  # the subscription, not a verb
}


@pytest.fixture(scope="module")
def payload() -> dict[str, bytes]:
    return skill_payload(SHARED_SKILLS["wui"])


def _verbs(payload: dict[str, bytes], example: str) -> set[str]:
    """Which `workspace.*` calls an example makes. Matched on the member name
    rather than the whole expression: both examples chain across lines, so
    `workspace.writeFile` as a literal is absent from code that calls it."""
    source = payload[f"examples/{example}/app.js"].decode()
    return set(re.findall(r"workspace\s*\.?\s*\n?\s*\.?(\w+)\(", source))


def test_skill_ships_both_examples_whole(payload: dict[str, bytes]):
    """Each example is a WUI in its own right: the marker, the entry, and the
    two files the entry references. A folder missing one of them teaches the
    agent a shape that does not run."""
    for name in EXAMPLES:
        for member in ("page.ai.yaml", "index.html", "app.js", "style.css"):
            assert f"examples/{name}/{member}" in payload, f"{name} is missing {member}"


def test_each_example_declares_itself_a_wui(payload: dict[str, bytes]):
    for name in EXAMPLES:
        text = payload[f"examples/{name}/page.ai.yaml"].decode()
        assert re.search(r"^view:\s*wui\s*$", text, re.MULTILINE), name


def test_each_example_loads_its_siblings_by_relative_path(payload: dict[str, bytes]):
    """The assembler only inlines a FOLDER-RELATIVE reference. An example using
    an absolute or remote one would render as a page with no styling and no
    behaviour, which is the failure this skill spends a section warning about."""
    for name in EXAMPLES:
        html = payload[f"examples/{name}/index.html"].decode()
        assert './app.js"' in html, name
        assert './style.css"' in html, name


def test_no_example_reaches_for_the_network(payload: dict[str, bytes]):
    """A WUI has no network. An example that fetched, or pulled a font or a
    script from a CDN, would be copied and then silently do nothing."""
    for name in EXAMPLES:
        for member in ("index.html", "app.js", "style.css"):
            body = payload[f"examples/{name}/{member}"].decode()
            assert "http://" not in body, f"{name}/{member}"
            assert "https://" not in body, f"{name}/{member}"
            assert "fetch(" not in body, f"{name}/{member}"


def test_examples_only_call_verbs_that_exist(payload: dict[str, bytes]):
    for name in EXAMPLES:
        used = _verbs(payload, name)
        assert used, f"{name} calls nothing — it would not need a WUI"
        assert used <= VERBS, f"{name} invents {sorted(used - VERBS)}"


def test_the_two_examples_are_the_two_shapes(payload: dict[str, bytes]):
    """They are not two of the same thing. The editor writes; the dashboard does
    not and navigates instead. Two write-shaped examples would leave the reading
    half — the one this platform is mostly for — unillustrated."""
    # Matched the way `test_examples_only_call_verbs_that_exist` does, because
    # both examples chain across lines — a literal `workspace.writeFile` is
    # absent from code that plainly calls it.
    editor = _verbs(payload, "editor")
    dashboard = _verbs(payload, "dashboard")

    assert "writeFile" in editor
    assert "writeFile" not in dashboard
    assert "deleteFile" not in dashboard
    assert "openFile" in dashboard


def test_both_examples_handle_a_failed_call(payload: dict[str, bytes]):
    """Every refusal arrives as a rejected promise carrying a sentence written
    for the reader. An example that dropped it would teach a page that goes
    blank and says nothing — the failure the whole error path exists to remove."""
    for name in EXAMPLES:
        app = payload[f"examples/{name}/app.js"].decode()
        assert ".catch(" in app, name


def test_the_skill_points_at_both_examples():
    """The table is how an agent chooses. A skill shipping an example it never
    names is a file nobody opens."""
    body = SHARED_SKILLS["wui"].joinpath("SKILL.md").read_text()
    for name in EXAMPLES:
        assert f"examples/{name}/" in body, name

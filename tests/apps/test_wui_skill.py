"""The WUI skill's shipped examples.

A skill's examples are the part an agent COPIES, so they are the part that has
to be complete and consistent — a reference that describes an API is checked by
reading, an example is checked by whether it would have run. Pinned here for the
reason this branch learned twice: a behaviour whose only proof is the code gets
deleted by the next change with nothing turning red.
"""

from __future__ import annotations

import json
import re

import pytest

from workspace_app.apps.shared_skills import SHARED_SKILLS
from workspace_app.apps.skill_payload import skill_payload

#: The examples whose files ARE the page — no build, so the entry and its two
#: siblings sit at the folder root.
EXAMPLES = ("dashboard", "editor", "external")

#: The built one. A different shape on purpose: its entry is the build OUTPUT,
#: its source is `src/`, and its root `index.html` is the bundler's template
#: rather than the page. Asserting the plain shape on it would either fail or,
#: worse, be loosened until it stopped holding for the other three.
BUILT = "react"

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


def test_the_examples_are_three_different_shapes(payload: dict[str, bytes]):
    """They are not three of the same thing. The editor writes; the dashboard
    does not and navigates instead; only the external one reaches outside the
    item. Three write-shaped examples would leave the reading half — the one
    this platform is mostly for — unillustrated."""
    # Matched the way `test_examples_only_call_verbs_that_exist` does, because
    # the examples chain across lines — a literal `workspace.writeFile` is
    # absent from code that plainly calls it.
    editor = _verbs(payload, "editor")
    dashboard = _verbs(payload, "dashboard")
    external = _verbs(payload, "external")

    assert "writeFile" in editor
    assert "writeFile" not in dashboard
    assert "deleteFile" not in dashboard
    assert "openFile" in dashboard
    assert "callTool" in external
    assert "callTool" not in editor and "callTool" not in dashboard


def test_only_the_tool_calling_example_declares_a_tool(payload: dict[str, bytes]):
    """`tools:` is a page's disclosure of what it reaches for. An example
    declaring one it never calls would teach the habit of asking for reach it
    does not need; one CALLING a tool it never declared would be refused, and a
    reader copying it would meet that refusal instead of a working page."""
    for name in EXAMPLES:
        yaml = payload[f"examples/{name}/page.ai.yaml"].decode()
        declares = bool(re.search(r"^tools:", yaml, re.MULTILINE))
        assert declares == ("callTool" in _verbs(payload, name)), name


def test_the_tool_example_tells_the_refusals_apart(payload: dict[str, bytes]):
    """Its whole reason to exist. The three failures send a reader to three
    different places — the view file, an operator, the tool's own output — and a
    page showing "Lookup failed" for all of them sends them to the wrong one
    twice out of three times. So it must NOT flatten the platform's message."""
    app = payload["examples/external/app.js"].decode()

    assert "exit_code" in app, "the tool's own failure is not the platform's"
    assert re.search(r"\.catch\(", app), "a refusal arrives as a rejection"
    assert "err.message" in app, "the refusal text must be shown, not replaced"


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

def test_the_built_example_points_at_its_build_output(payload: dict[str, bytes]):
    """`entry:` is the whole difference. Without it the renderer opens the
    folder's root `index.html` — the bundler's TEMPLATE, which loads
    `/src/main.jsx`, a dev-server path nothing can inline. The page renders
    empty and nothing says why."""
    yaml = payload[f"examples/{BUILT}/page.ai.yaml"].decode()

    assert re.search(r"^entry:\s*dist/index\.html\s*$", yaml, re.MULTILINE)


def test_the_built_example_carries_the_settings_that_fail_silently(payload: dict[str, bytes]):
    """Both are silent when wrong: the default `base` emits root-absolute asset
    URLs the assembler will not inline (blank page), and a code-split build
    leaves lazy chunks referenced only from JS the assembler never reads (breaks
    on the first navigation, not at build time)."""
    config = payload[f"examples/{BUILT}/vite.config.js"].decode()

    assert 'base: "./"' in config
    assert "inlineDynamicImports: true" in config


def test_the_built_example_declares_its_dependencies(payload: dict[str, bytes]):
    """A page whose libraries are declared can be rebuilt after the sandbox is
    recycled; one that had them installed ad hoc cannot."""
    pkg = json.loads(payload[f"examples/{BUILT}/package.json"])

    assert "react" in pkg["dependencies"]
    assert "vite" in pkg["devDependencies"]
    assert "build" in pkg["scripts"]


def test_the_built_example_says_the_rebuild_step_out_loud(payload: dict[str, bytes]):
    """The one silent failure on this path: editing `src/` changes nothing until
    a rebuild, and the user is left looking at the old page. Writing it down is
    the whole mitigation, so it has to actually be written down."""
    readme = payload[f"examples/{BUILT}/README.md"].decode()

    assert "rebuild" in readme.lower()
    assert "--frozen-lockfile" in readme


def test_the_built_example_reaches_the_bridge_the_same_way(payload: dict[str, bytes]):
    """A build changes how the page is produced, not what it can do. If this
    example implied a different API, it would teach one that does not exist."""
    source = payload[f"examples/{BUILT}/src/main.jsx"].decode()
    used = set(re.findall(r"workspace\s*\.?\s*\n?\s*\.?(\w+)\(", source))

    assert used, "it would not need a WUI"
    assert used <= VERBS, f"invents {sorted(used - VERBS)}"


def test_the_skill_offers_the_built_example_too():
    body = SHARED_SKILLS["wui"].joinpath("SKILL.md").read_text()

    assert f"examples/{BUILT}/" in body
    # And says which to prefer, because "you can build" is not the same as
    # "you should": the build step is the only thing here that can be forgotten.
    assert "Prefer no build" in body

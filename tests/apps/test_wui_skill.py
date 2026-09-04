"""The WUI skill's shipped examples.

A skill's examples are the part an agent COPIES, so they are the part that has
to be complete and consistent — a reference that describes an API is checked by
reading, an example is checked by whether it would have run. Pinned here for the
reason this branch learned twice: a behaviour whose only proof is the code gets
deleted by the next change with nothing turning red.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from workspace_app.apps.shared_skills import SHARED_SKILLS
from workspace_app.apps.skill_payload import skill_payload

#: The examples whose files ARE the page — no build, so the entry and its two
#: siblings sit at the folder root.
EXAMPLES = ("dashboard", "editor", "external", "chart")

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


def test_the_built_example_says_who_rebuilds_and_when(payload: dict[str, bytes]):
    """The one silent failure on this path: editing `src/` changes nothing until
    a rebuild, and the user is left looking at the old page.

    The pane now covers the person who OPENS the page — it rebuilds on open, and
    there is a button. It does not cover the person already looking at it while
    the agent edits: they press Refresh and see the old build. So the README has
    to say BOTH halves; saying only the automatic one would teach the agent it
    can skip the step that protects the reader in front of it."""
    readme = payload[f"examples/{BUILT}/README.md"].decode()

    assert "rebuild in the same turn as the edit" in readme.lower()
    assert "auto-rebuild" in readme.lower()
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


# ── the examples have to LOOK like something ──────────────────────────────


def _classes_used(markup: str) -> set[str]:
    """Every class the markup asks for, from `class=` and JSX `className=`."""
    used: set[str] = set()
    for attr in re.findall(r'class(?:Name)?="([^"{}]+)"', markup):
        used.update(attr.split())
    return used


def _classes_defined(css: str) -> set[str]:
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


@pytest.mark.parametrize("name", (*EXAMPLES, BUILT))
def test_every_example_ships_its_own_looks(payload: dict[str, bytes], name: str):
    """A page with no stylesheet is not "unstyled" — it is the browser's 1995
    defaults, and that is the FIRST thing the person who asked for the page
    sees. The react example shipped without one, so its built page rendered as
    Times New Roman headings and a grey submit button; it took a screenshot to
    notice, because nothing about it fails.

    The page has no network, so the stylesheet is a file in the folder — linked
    from `index.html` by hand, or imported from the source and emitted by the
    bundler."""
    css = [p for p in payload if p.startswith(f"examples/{name}/") and p.endswith(".css")]

    assert css, f"{name} ships no stylesheet"
    assert len(payload[css[0]]) > 300, f"{name}'s stylesheet is a stub"


@pytest.mark.parametrize(
    ("name", "markup"),
    [(e, f"examples/{e}/index.html") for e in EXAMPLES]
    + [(BUILT, f"examples/{BUILT}/src/main.jsx")],
)
def test_no_example_names_a_class_that_does_not_exist(
    payload: dict[str, bytes], name: str, markup: str
):
    """A `className` with no rule behind it is invisible: the element renders,
    unstyled, and nothing anywhere says the name was a typo or a leftover. The
    react example asked for `problem` and `empty` and defined neither."""
    used = _classes_used(payload[markup].decode())
    defined: set[str] = set()
    for path, body in payload.items():
        if path.startswith(f"examples/{name}/") and path.endswith(".css"):
            defined |= _classes_defined(body.decode())

    assert used <= defined, f"{name} uses classes nothing defines: {sorted(used - defined)}"


def test_the_charting_example_gets_its_library_from_the_folder(payload: dict[str, bytes]):
    """The question every dashboard starts with is "can I draw a chart", and the
    honest answer needs two halves: the library is a FILE IN THE FOLDER (a CDN
    reference never arrives), and the sandbox — which does have a network — is
    what puts it there. An example that hand-drew its chart would answer the
    first half by giving up on it."""
    html = payload["examples/chart/index.html"].decode()
    build = json.loads(payload["examples/chart/package.json"].decode())["scripts"]["build"]
    readme = payload["examples/chart/README.md"].decode()

    assert './chart.umd.js"' in html, "the page must reference the library beside it"
    assert "npm pack chart.js" in build, "the build step is what fetches the library"
    assert "chart.umd.js" in build
    assert "do not hand-draw" in readme.lower()


def test_the_charting_example_survives_a_library_that_is_not_there_yet(
    payload: dict[str, bytes],
):
    """Copied but not yet built, the global does not exist. A page that throws
    renders nothing, and the reader cannot tell whether the library or their own
    data is what is wrong — so it says which, in a sentence, and still shows the
    table."""
    app = payload["examples/chart/app.js"].decode()

    assert 'typeof Chart === "undefined"' in app
    assert "chart.umd.js is not in this folder yet" in app


def test_the_charting_example_gives_its_canvas_a_height(payload: dict[str, bytes]):
    """A canvas has no intrinsic size: in a box with no height the chart
    collapses to nothing, and nothing about that failure says so."""
    css = payload["examples/chart/style.css"].decode()

    assert re.search(r"\.plot\s*\{[^}]*height:", css, re.S), "the plot box needs a height"


def test_the_skill_names_every_example_it_ships(payload: dict[str, bytes]):
    """A count in prose drifts: the skill said "the three complete, working
    examples" for as long as it took to add two more, and an agent reading it
    would stop looking after the third. So the table, not a number, is the
    index — and every folder has to be in it.

    The other direction matters as much: a row pointing at a folder that is not
    shipped sends the agent to copy something that is not there."""
    skill = payload["SKILL.md"].decode()
    shipped = {p.split("/")[1] for p in payload if p.startswith("examples/")}
    listed = set(re.findall(r"`examples/([a-z-]+)/`", skill))

    assert shipped <= listed, f"the skill never mentions {sorted(shipped - listed)}"
    assert listed <= shipped, (
        f"the skill points at folders it does not ship: {sorted(listed - shipped)}"
    )
    # And no bare count to go stale beside the table.
    assert not re.search(r"the (?:two|three|four|five) complete", skill), (
        "a number in the prose will drift the next time an example is added"
    )


def test_the_user_facing_page_names_every_example_too(payload: dict[str, bytes]):
    """`docs/wui.md` carries its own table of examples, for the person the skill
    is NOT written for. It listed three while five shipped — and removing a
    stale count from the skill did nothing for it, because the skill's test only
    ever reads the skill."""
    doc = (pathlib.Path(__file__).resolve().parents[2] / "docs/wui.md").read_text()
    shipped = {p.split("/")[1] for p in payload if p.startswith("examples/")}
    listed = set(re.findall(r"`examples/([a-z-]+)/`", doc))

    assert shipped <= listed, f"docs/wui.md never mentions {sorted(shipped - listed)}"
    assert listed <= shipped, (
        f"docs/wui.md points at folders nothing ships: {sorted(listed - shipped)}"
    )

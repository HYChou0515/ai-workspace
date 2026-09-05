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

#: Every example that ships, DERIVED from what is on disk rather than typed out.
#:
#: A hand-written list drifts the moment somebody adds an example: the new folder
#: reaches every user's workspace while the checks below quietly skip it. That
#: happened — `complete/` shipped and was covered by exactly one test, the one
#: that asks whether the docs mention it, because the per-example checks were
#: parameterised over a tuple nobody remembered to extend.
_EXAMPLE_DIR = SHARED_SKILLS["wui"] / "examples"


def _example_names() -> tuple[str, ...]:
    return tuple(sorted(p.name for p in _EXAMPLE_DIR.iterdir() if p.is_dir()))


#: The built ones — a different SHAPE on purpose: the entry is the build OUTPUT,
#: the source is `src/`, and the root `index.html` is the bundler's template
#: rather than the page. Asserting the plain shape on these would either fail or,
#: worse, be loosened until it stopped holding for the others.
#:
#: Detected by the SHAPE — does it have a source folder — and not by the word
#: "build" in a `package.json`. `chart/`'s build script runs `npm pack chart.js@4`:
#: it vendors a library rather than bundling anything, its files ARE the page, and
#: reading the word moved it into the built set where six guards silently stopped
#: applying to it. A misclassification here removes assertions instead of failing
#: one, which is the worst way for a check to be wrong.
def _is_built(name: str) -> bool:
    return (_EXAMPLE_DIR / name / "src").is_dir()


#: The examples whose files ARE the page — no build, so the entry and its two
#: siblings sit at the folder root.
EXAMPLES = tuple(n for n in _example_names() if not _is_built(n))


#: The first built one, for the tests that assert on a single built example.
BUILT = "react"

#: Every built example, for the checks that apply to all of them.
BUILT_ALL = tuple(n for n in _example_names() if _is_built(n))


def test_an_example_counts_as_built_only_when_it_has_a_source_to_build() -> None:
    """The split decides which guards an example gets, so getting it wrong
    removes assertions rather than failing one.

    `chart/` has a `package.json` whose "build" runs `npm pack chart.js@4` — it
    vendors a library, it is not a bundler. It has no `src/`, and its
    `index.html` IS the page. Classifying on the WORD "build" therefore moved it
    into the built set, where it silently stopped being checked for: shipping
    whole, declaring `view: wui`, loading its siblings by relative path, calling
    only verbs that exist, declaring the tools it calls, handling a failed call
    — and NOT REACHING THE NETWORK, which is the guard `chart` is the only
    example that needs, since it is the only one that pulls a third-party
    library. All of that went green by no longer running.

    So the predicate is the SHAPE: a built example is one with a source folder.
    The hand-written `if b != "chart"` that used to paper over this was the
    diagnosis arriving and being ignored.
    """

    # Checked against an INDEPENDENT signal, not against the predicate. `_is_built`
    # IS `src/.is_dir()`, so asserting that a built example has a `src/` restates
    # the definition and cannot fail — which is how this test first shipped. A
    # bundler dependency is the thing that actually makes an example "built", and
    # it is decided in a different file by a different person.
    def _bundled(name: str) -> bool:
        pkg = _EXAMPLE_DIR / name / "package.json"
        return pkg.is_file() and '"vite"' in pkg.read_text()

    for name in BUILT_ALL:
        assert _bundled(name), (
            f"{name} is classed as built but pulls no bundler — its EXAMPLES guards "
            "were dropped and nothing failed"
        )
    for name in EXAMPLES:
        assert not _bundled(name), f"{name} bundles but is checked as a plain page"
        # And its files really ARE the page: the entry references its siblings
        # directly, rather than being a template the bundler rewrites.
        assert './app.js"' in (_EXAMPLE_DIR / name / "index.html").read_text(), name


#: What `dispatchWuiRequest` answers to. An example calling anything else would
#: be teaching the agent an API that does not exist — the one mistake a copied
#: example makes unrecoverable, because it looks authoritative.
#: Read from the SHIPPED runtime rather than typed out. A hand-written copy of a
#: set defined in another file goes stale silently: `startRun` was added to the
#: bridge and this list did not hear about it, so a correct example would have
#: been reported as inventing an API.
def _verbs_from_runtime() -> set[str]:
    src = (
        pathlib.Path(__file__).resolve().parents[2] / "web/src/renderers/wui/runtime.ts"
    ).read_text()
    # The page-facing object literal — `name: function (…)` inside `workspace`.
    body = src.split("window.workspace = {", 1)[-1]
    return set(re.findall(r"^\s{4}(\w+):", body, re.MULTILINE))


VERBS = _verbs_from_runtime()


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
    `/src/main.tsx`, a dev-server path nothing can inline. The page renders
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


def test_the_built_example_type_checks_before_it_bundles(payload: dict[str, bytes]):
    """Vite STRIPS types; it never checks them. So a TypeScript page whose build
    is `vite build` alone type-checks nowhere — the compiler is present, shipped,
    configured, and silent, and the build goes green over code it rejects.

    `strict` is pinned for `strictNullChecks` — `whoami()` really can answer
    `null`, and so can `getElementById`. It is NOT what makes the `readFile`
    union bite: narrowing a discriminated union works without it. The shipped
    tsconfig said otherwise until a review ran `tsc` with `strict` off and got
    the same `TS2339` either way, which is the difference between a reason and
    a story."""
    pkg = json.loads(payload[f"examples/{BUILT}/package.json"])
    tsconfig = payload[f"examples/{BUILT}/tsconfig.json"].decode()
    build = pkg["scripts"]["build"]

    assert "typescript" in pkg["devDependencies"]
    assert "tsc" in build and build.index("tsc") < build.index("vite build")
    assert '"strict": true' in tsconfig


def test_the_built_example_ships_the_bridge_typed(payload: dict[str, bytes]):
    """`window.workspace` is injected by the renderer, so there is no import to
    follow and nothing to infer from: without a declaration TypeScript here is
    ceremony. The two shapes it has to get right are the two that fail SILENTLY —
    a page that renders and is wrong is the worst outcome a WUI has, because its
    reader cannot open a console."""
    types = payload[f"examples/{BUILT}/src/wui.d.ts"].decode()

    # The union is the point: `.text` must not exist on the binary arm.
    assert 'kind: "text"' in types and 'kind: "binary"' in types
    assert "WuiReadResult" in types
    # And a tool's output is a STRING with no promise of JSON.
    assert "output: string" in types
    assert "exit_code: number" in types
    for verb in VERBS:
        assert f"{verb}(" in types, f"the bridge type omits {verb}"
    # Naming the verbs is not enough — a file that lied about every return type
    # would pass that loop. `whoami` is the one that bit: the bridge answers
    # `{user: ctx.me}` and `me` is `string | null` while the signed-in user is
    # still being resolved, so a page that asked on a cold open was handed a
    # `null` the compiler had certified as a `string`.
    assert "user: string | null" in types
    # And `read_only` is optional on the API type it passes straight through.
    assert "read_only?: boolean" in types


def test_every_shipped_copy_of_the_bridge_type_is_identical(payload: dict[str, bytes]):
    """`wui.d.ts` describes the PLATFORM and is copied verbatim into pages. More
    than one copy ships, and two copies that may disagree eventually will — one
    gains a verb and the other does not, and an author copying the stale one is
    told an API does not exist when it does.

    Caught for real: `startRun` was added to one example's copy and not the
    other's, and only a check like this notices.
    """
    copies = {p: b for p, b in payload.items() if p.endswith("/wui.d.ts")}

    assert len(copies) > 1, "only one copy — this guard is measuring nothing"
    assert len(set(copies.values())) == 1, f"these disagree: {sorted(copies)}"


def _fields_the_platform_actually_emits() -> set[str]:
    """Every field name declared on any event the page can receive, read from
    the platform's own event modules.

    Derived, because the failure this guards against is invisible from inside
    the example: reading `e.exit_code` off an event that has no such field
    yields `undefined` forever, so the branch is permanently false and the page
    silently never reports a failure. TypeScript cannot see it either — the
    event arrives as `unknown` and the example casts it.
    """
    import ast

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "workspace_app"
    out: set[str] = set()
    for mod in (root / "api" / "events.py", root / "workflow" / "events.py"):
        for node in ast.walk(ast.parse(mod.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    out.add(stmt.target.id)
    return out


def test_the_worked_reducer_only_reads_fields_that_exist(payload: dict[str, bytes]):
    """The example is COPIED into pages. A field it reads that no event carries
    is a branch that can never be true, in code that looks authoritative.

    Caught for real: `done` was tested for `exit_code`, which `RunDone` does not
    have — so `failed` was always false and a failed run rendered as a finished
    one. Nothing could have failed except a check like this.
    """
    source = payload["examples/complete/src/workspace.ts"].decode()
    body = source.split("export function reduceRunEvent", 1)[-1].split("\nexport ", 1)[0]

    read = set(re.findall(r"\be\.(\w+)", body))
    declared = _fields_the_platform_actually_emits()

    assert read, "the reducer reads nothing — this guard is measuring nothing"
    assert read <= declared, f"reads fields no event declares: {sorted(read - declared)}"


def test_the_worked_reducer_relays_the_platforms_own_sentence(payload: dict[str, bytes]):
    """`RunError` carries `message`. A reducer reading `text` there always falls
    through to its own wording, which throws away the one sentence that says
    what went wrong — the exact thing this file's `sentence()` helper exists to
    preserve, and which its docstring claims it does."""
    source = payload["examples/complete/src/workspace.ts"].decode()
    error_branch = source.split('e.type === "error"', 1)[-1].split("}", 1)[0]

    assert "e.message" in error_branch, "the error branch does not read `message`"


def test_the_skill_tells_a_page_how_to_declare_a_schedule():
    """The whole point of the third round is that a domain expert can say "every
    weekday at 09:00, build my report" without anyone editing the repo. The only
    way that happens is a PAGE writing `schedules.json` — and the page is written
    by an LLM reading this skill.

    So a schedule engine the skill never mentions is an engine nothing will ever
    use. It shipped that way: the filename, its shape and its words appeared in
    the plan and the Python source and NOWHERE a page author could find them.
    """
    body = SHARED_SKILLS["wui"].joinpath("SKILL.md").read_text()
    reference = SHARED_SKILLS["wui"].joinpath("reference.md").read_text()

    assert "schedules.json" in body, "the skill never names the file"
    # The words the parser actually accepts. A page that invents `cron:` or
    # `time:` writes a file that lints clean row-by-row and never fires.
    for word in ("every", "run", "with"):
        assert f"`{word}`" in reference, f"reference.md never names `{word}`"
    assert "minutes" in reference and "weekly" in reference
    # `n` is required by `every: minutes` and by nothing else — the one shape a
    # page gets wrong without being told.
    assert "`n`" in reference

    # The credential rule, which is invisible until it is 3am. A page written as
    # if a personal token is always there works perfectly while somebody is
    # clicking and fails every night — the "it worked when I tested it" bug.
    assert "personal token" in body or "personal token" in reference


def test_the_skill_makes_the_agent_look_at_a_tools_real_output():
    """`callTool` returns whatever the command printed. The shape is the tool's
    contract and the platform promises nothing about it, so an agent that writes
    `JSON.parse` against the tool's NAME ships a blank page to somebody who
    cannot open a console and can only report "it's broken".

    The agent holds the same tool — a page can only call what the item's agent
    can call — so "run it and look" is available, cheap, and the only thing that
    turns a guess into a fact."""
    body = SHARED_SKILLS["wui"].joinpath("SKILL.md").read_text()
    reference = SHARED_SKILLS["wui"].joinpath("reference.md").read_text()

    assert "Run the tool before you parse it" in body
    # The specific misuse a real deploy hit: a tool with a large result writes a
    # file and prints the PATH, so `output` is a filename. Parsing it "works"
    # and yields no rows; reading it against the wrong root fails and, caught
    # the way a first run is caught, shows as a permanent silent "nothing
    # found". Naming the case is the only thing that stops both.
    assert "When the tool answers with a PATH" in body
    # The two failures that shape actually produces, both silent: parsing the
    # path itself (which SUCCEEDS and yields no rows), and reading a workspace
    # path as a bare one, which puts the page's folder on TWICE. The doubled name
    # is the only thing on screen when it happens, so both docs must name it.
    assert "puts the folder on twice" in body
    assert "leading `/` for anything a tool named" in body
    assert "reads /lot-tracker/lot-tracker/out.json" in reference
    # Not just "be careful": the instruction has to be an ACTION, and it has to
    # say what to do when running it is impossible, or the agent guesses anyway.
    assert "You hold the same tool" in body
    assert "ask for one real example of its output" in body
    # And the reference must say the same thing at the same volume. It used to
    # carry this in a trailing parenthesis after "so it is safe to parse", which
    # is the sentence an agent acts on.
    assert "Run the tool yourself before writing the parser" in reference
    assert "the tool's contract, not the platform's)" not in reference


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
    # EVERY source file of every example, not one path. `complete/` keeps its
    # bridge calls in `src/workspace.ts`, so a check reading only `main.tsx`
    # skipped the file where all of them live — and an invented verb there is
    # the one mistake a copied example makes unrecoverable, because it looks
    # authoritative.
    used: set[str] = set()
    for name in (*EXAMPLES, *BUILT_ALL):
        for path, body in payload.items():
            if not path.startswith(f"examples/{name}/"):
                continue
            if not path.endswith((".js", ".ts", ".tsx", ".html")):
                continue
            used |= set(
                re.findall(r"workspace\s*\.?\s*\n?\s*\.?(\w+)\(", body.decode("utf-8", "replace"))
            )

    assert used, "it would not need a WUI"
    assert used <= VERBS, f"invents {sorted(used - VERBS)}"


def test_the_skill_offers_the_built_example_too():
    body = SHARED_SKILLS["wui"].joinpath("SKILL.md").read_text()

    assert f"examples/{BUILT}/" in body
    # And says which to prefer. This one is the DEFAULT now: auto-rebuild
    # removed the cost that made a build worth avoiding, and what is left is a
    # compiler on a page whose reader cannot open a console.
    assert "Default to `examples/react/`" in body


# ── the examples have to LOOK like something ──────────────────────────────


def _classes_used(markup: str) -> set[str]:
    """Every class the markup asks for, from `class=` and JSX `className=`."""
    used: set[str] = set()
    for attr in re.findall(r'class(?:Name)?="([^"{}]+)"', markup):
        used.update(attr.split())
    return used


def _classes_defined(css: str) -> set[str]:
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


@pytest.mark.parametrize("name", (*EXAMPLES, *BUILT_ALL))
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

    # And something has to REACH it. A file sitting in the folder that nothing
    # links or imports is the same as no stylesheet at all — which is exactly
    # how the react example first shipped looking like 1995 while this check
    # was green. Asserted on the whole example, because which file does the
    # referencing depends on its shape: `index.html` links it, or the source
    # imports it and the bundler emits the link.
    stem = css[0].rsplit("/", 1)[-1]
    referenced = any(
        stem in payload[p].decode("utf-8", "replace")
        for p in payload
        if p.startswith(f"examples/{name}/") and not p.endswith(".css")
    )
    assert referenced, f"{name} ships {stem} but nothing references it"


@pytest.mark.parametrize(
    ("name", "markup"),
    # Every example, whichever shape it has: a no-build one declares its classes
    # in `index.html`, a built one in its source. Listing only ONE built example
    # here is how `complete/` shipped with this check skipping it entirely.
    [(e, f"examples/{e}/index.html") for e in EXAMPLES]
    + [(b, f"examples/{b}/src/main.tsx") for b in BUILT_ALL],
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

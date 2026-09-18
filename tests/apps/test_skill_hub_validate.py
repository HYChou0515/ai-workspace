"""What publishing checks before anything reaches the hub.

Two functions, both pure. `validate_skill_payload` returns the STRUCTURAL
problems — the ones the loader would silently skip over, so a skill that
publishes with one of them is a skill nobody's agent will ever read, with no
error anywhere. `referenced_tools` finds the registered tool names a body
mentions, so installing into an app that lacks one can be announced (plan Q1,
Q2). The registry is injected, not imported: the function is pure and the
tests say exactly what "registered" means.
"""

from __future__ import annotations

import pytest

from workspace_app.apps.skill_hub import (
    SKILL_HUB_MAX_BYTES,
    referenced_tools,
    skill_size_problem,
    validate_skill_payload,
)
from workspace_app.apps.skills import (
    SKILL_BODY_CAP,
    WORKSPACE_SKILL_DIR,
    workspace_skill_metas,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore

KNOWN = frozenset({"exec", "ask_user", "read_file", "kb_search", "save_skill"})


def _md(
    name: str = "triage-reflow", description: str = "Triage reflow defects.", body: str = "# How\n"
) -> bytes:
    front = f"---\nname: {name}\n"
    if description is not None:
        front += f"description: {description}\n"
    return (front + "---\n\n" + body).encode()


# ── structure ────────────────────────────────────────────────────────────────


def test_a_well_formed_skill_has_no_problems() -> None:
    assert validate_skill_payload("triage-reflow", {"SKILL.md": _md()}) == []


def test_a_folder_without_a_skill_md_is_not_a_skill() -> None:
    problems = validate_skill_payload("triage-reflow", {"references/x.md": b"x"})
    assert problems and "SKILL.md" in problems[0]


def test_the_frontmatter_name_must_equal_the_folder_name() -> None:
    """Trap 1 of the three the docs list. The loader keys the folder, the
    frontmatter names it, and a mismatch is a silent skip."""
    problems = validate_skill_payload("triage-reflow", {"SKILL.md": _md(name="reflow-triage")})
    assert len(problems) == 1
    assert "reflow-triage" in problems[0] and "triage-reflow" in problems[0]


def test_a_missing_description_is_a_problem() -> None:
    """Trap 2. The index shows name + description and nothing else; with no
    description the agent has nothing to match the user's words against."""
    raw = b"---\nname: triage-reflow\n---\n\n# How\n"
    problems = validate_skill_payload("triage-reflow", {"SKILL.md": raw})
    assert len(problems) == 1 and "description" in problems[0]


def test_a_body_over_the_cap_is_a_problem() -> None:
    raw = _md(body="x" * (SKILL_BODY_CAP + 1))
    problems = validate_skill_payload("triage-reflow", {"SKILL.md": raw})
    assert len(problems) == 1 and str(SKILL_BODY_CAP) in problems[0]


def test_a_reference_the_body_names_must_ship() -> None:
    """A `see references/glossary.md` with no such file installs as a dangling
    pointer: the agent follows it, `read_file` fails, and the skill looks broken
    to the first person who uses it rather than to the one who published it."""
    raw = _md(body="See references/glossary.md and references/steps.md.\n")
    problems = validate_skill_payload(
        "triage-reflow", {"SKILL.md": raw, "references/glossary.md": b"ok"}
    )
    assert len(problems) == 1
    assert "references/steps.md" in problems[0]
    assert "references/glossary.md" not in problems[0], "the one that ships is not a problem"


def test_a_reference_at_the_end_of_a_sentence_is_the_file_not_the_file_plus_a_dot() -> None:
    """Review round 1: `First read references/glossary.md. Then …` was refused
    for not shipping `references/glossary.md.` — the full stop rode along."""
    raw = _md(body="First read references/glossary.md. Then continue.\n")
    assert (
        validate_skill_payload("triage-reflow", {"SKILL.md": raw, "references/glossary.md": b"ok"})
        == []
    )
    # …and the missing-file report names the file, not the file plus a dot.
    problems = validate_skill_payload("triage-reflow", {"SKILL.md": raw})
    assert len(problems) == 1 and "`references/glossary.md`" in problems[0]


@pytest.mark.parametrize(
    "body",
    [
        "Read **references/g.md** first.",
        "Read *references/g.md* first.",
        "Read _references/g.md_ first.",
        "Did you read references/g.md? Then continue.",
        "Read references/g.md!",
        "Read references/g.md/ and go on.",
        "See references/g.md... later.",
        "(references/g.md)",
        "[the glossary](references/g.md)",
        "`references/g.md`",
    ],
)
def test_prose_punctuation_around_a_reference_is_not_part_of_the_file_name(body: str) -> None:
    """Review round 2: round 1 stripped the full stop and nothing else, so
    `**references/g.md**` was refused for not shipping `references/g.md**`.
    Whatever prose wraps a path, the file the body names is the one the
    folder ships — the folder decides that; a punctuation class only shapes
    the name in the sentence when nothing shipped matches."""
    raw = _md(body=body + "\n")
    shipped = {"SKILL.md": raw, "references/g.md": b"ok"}
    assert validate_skill_payload("triage-reflow", shipped) == []
    problems = validate_skill_payload("triage-reflow", {"SKILL.md": raw})
    assert len(problems) == 1 and "`references/g.md`" in problems[0], problems


@pytest.mark.parametrize(
    ("body", "shipped", "named"),
    [
        ("See references/日本.md.", (), "references/日本.md"),
        ("See references/résumé.", (), "references/résumé"),
        ("See references/日本", (), "references/日本"),
        ("See references/a日本 now.", ("references/a",), "references/a日本"),
        ("See references/g.md-extra.md.", ("references/g.md",), "references/g.md-extra.md"),
        ("See references/data_ now.", ("references/data_",), None),
    ],
)
def test_a_mention_outside_ascii_is_named_whole(
    body: str, shipped: tuple[str, ...], named: str | None
) -> None:
    """Round 3: the word class was ASCII-only, so `references/日本` was
    reported as `references` and `references/a日本` resolved to a shipped
    `references/a`. Word characters are Unicode's, the folder is asked
    first, and the sentence names the file the body names. The
    `references/g.md-extra.md` row pins the word-character check itself: a
    shipped file is only the one a mention names when nothing but punctuation
    follows it (round 3 found that check unpinned — without it the refusal
    vanished)."""
    raw = _md(body=body + "\n")
    payload = {"SKILL.md": raw, **{rel: b"x" for rel in shipped}}
    problems = validate_skill_payload("triage-reflow", payload)
    if named is None:
        assert problems == []
    else:
        assert len(problems) == 1 and f"`{named}`" in problems[0], problems


@pytest.mark.parametrize("name", ["a/b", ".", ".."])
def test_a_name_that_is_not_one_folder_is_refused_by_name(name: str) -> None:
    """Review round 2: `name: a/b` published and installed as `.skill/a/b/`,
    which `workspace_skill_metas` (one level deep) never lists — the reply
    then promised an index entry that could not exist. `.` and `..` are
    refused for a reason the memory-backed loader below cannot see: on a real
    disk `.skill/./` IS `.skill/` and `.skill/../SKILL.md` is the workspace
    root, so an entry so named would install outside its folder."""
    raw = f"---\nname: {name}\ndescription: d\n---\n\nbody\n".encode()
    problems = validate_skill_payload(name, {"SKILL.md": raw})
    assert len(problems) == 1 and f"`{name}`" in problems[0], problems
    # The reason is the one that applies (round 3: `.` was told "no `/`").
    assert ("no `/`" in problems[0]) == ("/" in name), problems


@pytest.mark.parametrize("folder", ["a/b", "../x", "", ".dotted", "with space", "ok-name"])
async def test_the_validator_agrees_with_the_loader_about_names(folder: str) -> None:
    """PARITY over the folder name, the loader as oracle (the table above
    varies the payload with the name fixed; this varies the name). The
    validator's name rule is not a list of characters somebody thought
    unwise — it is exactly "would the loader list this folder": a name with a
    `/` is two levels deep and never listed; `.dotted` and `with space` are
    listed, so they pass. (`.` / `..` are the one exception, above.)"""
    raw = f"---\nname: {folder}\ndescription: d\n---\n\nbody\n".encode()
    loader_says_yes = await _loader_lists(folder, {"SKILL.md": raw})
    validator_says_yes = validate_skill_payload(folder, {"SKILL.md": raw}) == []
    assert loader_says_yes == validator_says_yes, (
        f"{folder!r}: loader lists it = {loader_says_yes}, validator passes it = "
        f"{validator_says_yes}"
    )


def test_a_script_that_does_not_parse_is_a_problem() -> None:
    """`scripts/*.py` run through `exec` in the workspace. One that cannot even
    parse fails on first use — cheap to catch here, expensive to find later."""
    payload = {
        "SKILL.md": _md(),
        "scripts/ok.py": b"print('hi')\n",
        "scripts/bad.py": b"def (\n",
    }
    problems = validate_skill_payload("triage-reflow", payload)
    assert len(problems) == 1
    assert "scripts/bad.py" in problems[0] and "scripts/ok.py" not in problems[0]


def test_the_size_cap_is_stated_from_sizes_alone() -> None:
    """Review round 1 set the cap (an entry's files are read whole into memory
    on publish, stored outside any user quota, and every installer's workspace
    pays for them); round 2 moved the check BEFORE the read, so it is a rule
    over sizes, not bytes. One bound, stated in the refusal."""
    over = {"SKILL.md": len(_md()), "assets/huge.bin": SKILL_HUB_MAX_BYTES + 1}
    problem = skill_size_problem(over)
    assert problem is not None and "MiB" in problem

    fits = {"SKILL.md": len(_md()), "assets/big.bin": SKILL_HUB_MAX_BYTES - len(_md())}
    assert skill_size_problem(fits) is None


def test_every_problem_is_reported_not_just_the_first() -> None:
    """The publisher reads this once, in the chat, and fixes everything it
    names. Stopping at the first would cost a round trip per trap."""
    raw = b"---\nname: wrong\n---\n\nSee references/none.md\n"
    problems = validate_skill_payload(
        "triage-reflow", {"SKILL.md": raw, "scripts/bad.py": b"def (\n"}
    )
    assert len(problems) == 4, problems


# ── referenced tools ─────────────────────────────────────────────────────────


def test_a_tool_named_in_a_code_span_is_found() -> None:
    assert referenced_tools("Run `exec` on the script, then `ask_user`.", KNOWN) == [
        "ask_user",
        "exec",
    ]


def test_a_tool_named_as_a_bare_word_is_found() -> None:
    assert referenced_tools("Use exec to run it.", KNOWN) == ["exec"]


def test_a_registered_name_inside_another_word_is_not_a_hit() -> None:
    """`exec` inside `executive` is not a tool reference. Whole-word or
    code-span only; a substring match would tag half the skills in the hub
    with `exec`."""
    assert referenced_tools("The executive summary. Preexecution notes.", KNOWN) == []


def test_an_unregistered_name_is_not_a_hit_even_in_a_code_span() -> None:
    """The registry is the registry. A body that says `deploy_rocket` is
    talking about something else, not asking for a tool."""
    assert referenced_tools("Call `deploy_rocket` and `read_file`.", KNOWN) == ["read_file"]


def test_the_frontmatter_is_not_scanned() -> None:
    """A description that says "uses exec" is describing, not calling."""
    body = "---\nname: x\ndescription: uses exec heavily\n---\n\nNo tools here.\n"
    assert referenced_tools(body, KNOWN) == []


def test_hits_are_sorted_and_unique() -> None:
    assert referenced_tools("`read_file` then `exec` then `read_file` again", KNOWN) == [
        "exec",
        "read_file",
    ]


# ── parity with the real loader ──────────────────────────────────────────────


async def _loader_lists(folder: str, payload: dict[str, bytes]) -> bool:
    """Write the payload where an install would and ask the REAL loader."""
    files, ws = WorkspaceFiles(MemoryFileStore()), "inv-1"
    for rel, data in payload.items():
        await files.write(ws, f"/{WORKSPACE_SKILL_DIR}/{folder}/{rel}", data)
    return any(m.name == folder for m in await workspace_skill_metas(files, ws))


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("well-formed", {"SKILL.md": _md()}),
        ("name mismatch", {"SKILL.md": _md(name="reflow-triage")}),
        ("no frontmatter at all", {"SKILL.md": b"# just a heading\n"}),
    ],
)
async def test_the_validator_agrees_with_the_loader(label: str, payload: dict[str, bytes]) -> None:
    """PARITY: what this validator passes, the workspace loader lists; what it
    refuses for a loader-visible reason, the loader skips.

    The validator exists so the publisher hears about a trap BEFORE it silently
    swallows a skill. That promise is only true if the two grade the same
    inputs the same way — and a validator kept alike by hand is exactly the
    disagreement `validate_user_schedules` vs `usable_rows` turned out to be
    (P44). The loader is the oracle here; the validator is what is under test.

    Only the loader-visible rules are in this table. The cap, dangling
    references and unparseable scripts are things the loader does NOT check
    (it loads them and they fail later), which is why the validator checks them
    — those are covered by their own tests above, not by parity.
    """
    loader_says_yes = await _loader_lists("triage-reflow", payload)
    validator_says_yes = validate_skill_payload("triage-reflow", payload) == []
    assert loader_says_yes == validator_says_yes, (
        f"{label}: loader lists it = {loader_says_yes}, validator passes it = "
        f"{validator_says_yes} — the two disagree about one file"
    )


async def test_the_validator_is_stricter_than_the_loader_about_description_on_purpose() -> None:
    """The ONE intentional divergence, stated rather than left out of the table.

    The workspace loader lists a skill with no description — `_workspace_skill_meta`
    checks `name` and nothing else, so `description=""` loads fine. But a
    skill with no description is the purest form of the debug loop the hub's
    review exists to cut: it appears in the agent's index and can never be
    matched against anything the user says, so it is "installed" and never
    used, with no error anywhere. Publishing that to everyone is worse than
    failing to load it.

    So the validator refuses it while the loader accepts it. Measured here so
    the parity test above cannot be read as "the two always agree" — they agree
    on the loader's rules; this rule is the hub's own.
    """
    payload = {"SKILL.md": b"---\nname: triage-reflow\n---\n\n# How\n"}
    assert await _loader_lists("triage-reflow", payload) is True, (
        "the loader stopped listing it — update this test's premise"
    )
    assert validate_skill_payload("triage-reflow", payload) != [], (
        "the validator no longer refuses it"
    )


# ── the install告知 against an App's ceiling ─────────────────────────────────


def test_a_tool_the_platform_grants_every_app_implicitly_is_never_missing() -> None:
    """Review round 1: `read_skill` is in the registry (so the scanner finds
    it) but in no App's `agent.tools` — `build_tools` grants it on its own —
    so every skill that said "load it with read_skill" was flagged as needing
    a tool the App lacked. The ceiling is the manifest PLUS what the platform
    adds without being asked."""
    from workspace_app.apps.skill_hub import missing_tools_for

    assert missing_tools_for(["exec", "read_skill", "query_entity"], "rca") == ["query_entity"]


def test_the_implicit_grant_list_matches_what_build_tools_adds_on_its_own() -> None:
    """The constant beside `missing_tools_for` names the tools `build_tools`
    grants without an App declaring them. Pinned against the builder itself:
    build the tools for a turn whose `allowed_tools` is EMPTY, and whatever
    comes back is the implicit set — a new implicit grant that is not in the
    constant would be reported as "this App lacks it" on every skill hub row."""
    from workspace_app.agent.tools import build_tools
    from workspace_app.apps.skill_hub import IMPLICITLY_GRANTED_TOOLS

    implicit = {
        t.name for t in build_tools([], app_slug="rca", profile="default", skills_reachable=True)
    }
    assert implicit == IMPLICITLY_GRANTED_TOOLS

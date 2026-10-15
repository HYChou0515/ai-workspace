"""The catalog entry for a command the turn has not loaded yet
(`docs/plan-tool-on-demand-loading.md` P1).

A turn sends every granted command's full JSON schema today, which is what puts
a six-figure character count in one system prompt. The catalog replaces that
with one line per command: the real name, the author's own sentence, and no
parameter schema at all — the schema arrives when the command is actually used.

The trim is deliberately NOT a character cut: a description cut mid-sentence is
how the model stops being able to tell two commands apart. Nothing is lost
either way — loading the command hands over the full description and schema.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from agents.tool_context import ToolContext
from agents.usage import Usage

from workspace_app.agent.context import AgentToolContext
from workspace_app.tooling.registry import (
    CommandInfo,
    PackageInfo,
    build_catalog_tools,
    build_function_tools,
    build_load_tool,
    catalog_description,
    describe_command,
    load_reply,
)


def _pkg(*, third_party: bool = False) -> PackageInfo:
    return PackageInfo(
        name="rca-tools",
        install_dir="../.tools/rca-tools",
        commands=(
            CommandInfo(
                "data-fetch",
                "Fetch a lot's history. Accepts a lot id and a date range.",
                {
                    "type": "object",
                    "properties": {"lot": {"type": "string"}, "days": {"type": "integer"}},
                    "required": ["lot"],
                },
            ),
            CommandInfo("plot-trend", "Plot a trend.", {"type": "object", "properties": {}}),
        ),
        description="SMT 產線的量測資料擷取與初步分析。",
        third_party=third_party,
    )


def test_nothing_said_stays_nothing():
    assert catalog_description("") == ""
    assert catalog_description("   \n ") == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Short enough: verbatim, no ellipsis — an author who wrote one line
        # gets that line.
        ("Fetch a wafer's history.", "Fetch a wafer's history."),
        ("抓取晶圓的歷史紀錄。", "抓取晶圓的歷史紀錄。"),
        # Several sentences: the FIRST one is what decides whether to call this
        # command; the rest is detail that arrives with the schema.
        (
            "Fetch a wafer's history. Accepts a lot id and a date range. Returns one row per step.",
            "Fetch a wafer's history.",
        ),
        ("抓取晶圓的歷史紀錄。可以給批號與日期範圍。", "抓取晶圓的歷史紀錄。"),
        # A newline ends a sentence too — plenty of descriptions are written as
        # a title line plus a paragraph.
        ("Summarise a CSV column\n\nUse this before plotting.", "Summarise a CSV column"),
    ],
)
def test_the_first_sentence_is_the_entry(text, expected):
    assert catalog_description(text) == expected


def test_a_long_first_sentence_ends_at_a_word_boundary_not_mid_word():
    text = (
        "Fetch the complete processing history for a wafer including every step, "
        "every tool it visited and every measurement taken along the way"
    )
    out = catalog_description(text)

    assert len(out) <= 100
    assert out.endswith("…")
    # The cut lands between words: no half word before the ellipsis.
    assert text.startswith(out[:-1].rstrip())
    assert out[-2] != " "
    kept = out[:-1].rstrip()
    assert text[len(kept)] in " ", f"cut mid-word: {out!r}"


def test_a_long_chinese_first_sentence_ends_at_punctuation():
    text = (
        "抓取這片晶圓從投片到出貨的完整處理歷程，包含每一個站點、每一台機台、"
        "每一次量測的數值與時間，並附上每一步的作業員與配方版本、重工次數與原因，"
        "最後給一份可以直接貼進報告的摘要，附上每一站的良率與缺陷分布，供後續比對與追溯"
    )
    assert len(text) > 100, "the fixture has to be longer than the limit to walk this path"
    out = catalog_description(text)

    assert len(out) <= 100
    assert out.endswith("…")
    # Chinese has no spaces, so the boundary is a punctuation mark the author
    # wrote — cutting between two characters of a clause is the thing this
    # avoids.
    assert out[-2] in "，、；：", f"cut mid-clause: {out!r}"


def test_a_run_with_no_boundary_at_all_is_cut_at_the_limit_as_a_last_resort():
    # No space, no punctuation, longer than the limit: there is no boundary to
    # respect, so the limit wins. The entry is a hint for choosing a command,
    # and the full text is one `load` away.
    text = "x" * 250
    out = catalog_description(text)

    assert len(out) == 100
    assert out == "x" * 99 + "…"


def test_the_limit_is_a_parameter_not_a_constant_in_the_cut():
    text = "Fetch the complete processing history for a wafer including every step"
    assert len(catalog_description(text, limit=30)) <= 30
    assert catalog_description(text, limit=1000) == text


# ── the stub a turn sends for a command it has not loaded ────────────────


def test_a_catalog_tool_carries_the_name_and_the_line_and_no_schema():
    """What replaces today's full schema: the real name the model must call, one
    line of what it is, and NO parameters — the schema is the expensive half."""
    (fetch, plot) = build_catalog_tools([_pkg()], allowed=None, mark_loaded=lambda *_: None)

    assert fetch.name == "data-fetch"
    # The author's first sentence, plus which bundle it comes from — `load_tool`
    # advertises loading a whole bundle by name, and before round 1 A5 the turn
    # was never told any bundle name.
    assert fetch.description == "Fetch a lot's history. [rca-tools]"
    # An empty object, not the command's real schema. `additionalProperties` is
    # open on purpose: the model WILL guess arguments at this entry (the name is
    # right there), and a provider enforcing a closed schema would reject the
    # call before our handler could answer with the real one.
    assert fetch.params_json_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    assert fetch.strict_json_schema is False
    assert plot.name == "plot-trend"


def test_a_third_party_entry_does_not_repeat_the_bundle_it_already_names():
    """`rca-tools__data-fetch` already says the bundle; appending `[rca-tools]`
    would spend tokens on the same word twice."""
    (fetch, _) = build_catalog_tools(
        [_pkg(third_party=True)], allowed=None, mark_loaded=lambda *_: None
    )

    assert fetch.description == "Fetch a lot's history."


def test_a_third_party_catalog_tool_keeps_the_prefixed_name():
    # #888: the model's name for a third-party command is `<local>__<cmd>`, and
    # the catalog has to advertise the name that can actually be called.
    names = {
        t.name
        for t in build_catalog_tools(
            [_pkg(third_party=True)], allowed=None, mark_loaded=lambda *_: None
        )
    }
    assert names == {"rca-tools__data-fetch", "rca-tools__plot-trend"}


async def test_calling_a_catalog_tool_loads_it_and_answers_with_the_real_schema():
    """The model calling an unloaded command is EXPECTED, not a misuse — the
    catalog handed it the name. So the entry's answer is the schema plus "call
    it again", never a refusal (#537: a refusal reads as "stop trying")."""
    loaded: list[object] = []
    (fetch, _) = build_catalog_tools(
        [_pkg()], allowed=None, mark_loaded=lambda ctx, name: loaded.append((ctx, name))
    )
    # The real `ToolContext` the SDK hands a tool, so the handler reads
    # `.context` exactly the way it does in production.
    actx = AgentToolContext(investigation_id="i")
    tctx: ToolContext[Any] = ToolContext(
        context=actx,
        tool_name="data-fetch",
        tool_call_id="id",
        tool_arguments='{"lot": "guessed"}',
        usage=Usage(),
    )

    reply = await fetch.on_invoke_tool(tctx, '{"lot": "guessed"}')

    # The callback is handed the whole `ToolContext`, not just `.context`: the
    # caller also needs the call id, to keep this call's answer (a schema) out
    # of the stored thread (`docs/plan-tool-on-demand-loading.md`).
    assert loaded == [(tctx, "data-fetch")]
    # The full description — the catalog line was only the first sentence.
    assert describe_command(_pkg(), _pkg().commands[0]) in reply
    # The real schema, verbatim enough that the model can satisfy it.
    assert json.dumps(_pkg().commands[0].params_json_schema, ensure_ascii=False) in reply
    assert "data-fetch" in reply


async def test_load_tool_can_only_reach_what_the_executable_builder_grants():
    """The third builder, against the same oracle.

    `build_load_tool` filters with the same `_granted`, and nothing proved it:
    the review changed its `allowed` to `None` and all 42 targeted tests stayed
    green, while a turn granted one command could then load the schema of a
    command it was never given (round 1 G1). Parity, not an argument.
    """
    pkgs = [_pkg(), dataclasses.replace(_pkg(third_party=True), name="other-tools")]
    for allowed in (None, [], ["rca-tools"], ["rca-tools:data-fetch"], ["other-tools"]):
        executable = {t.name for t in build_function_tools(pkgs, allowed=allowed)}
        load = build_load_tool(pkgs, allowed=allowed, mark_loaded=lambda *_: None)
        ctx = AgentToolContext(investigation_id="i")
        # Ask for EVERY command of every package, granted or not.
        every = [
            name
            for pkg in pkgs
            for cmd in pkg.commands
            for name in (f"{pkg.name}__{cmd.name}", cmd.name, f"{pkg.name}:{cmd.name}", pkg.name)
        ]
        await load.on_invoke_tool(_tool_ctx(ctx, "load_tool"), json.dumps({"names": every[:20]}))

        assert ctx.loaded_tools <= executable, (allowed, ctx.loaded_tools - executable)


def test_the_catalog_names_exactly_what_the_executable_builder_grants():
    """Parity, with `build_function_tools` as the oracle: a stub for a command
    the real builder would drop is a tool that can never run, and a command the
    catalog omits is one the model will never know to ask for. Same selection
    code, not two copies kept alike by hand."""
    pkgs = [_pkg(), dataclasses.replace(_pkg(third_party=True), name="other-tools")]
    for allowed in (
        None,
        [],
        ["rca-tools"],
        ["rca-tools:data-fetch"],
        ["nope"],
        ["rca-tools:nope"],
    ):
        catalog = {
            t.name for t in build_catalog_tools(pkgs, allowed=allowed, mark_loaded=lambda *_: None)
        }
        executable = {t.name for t in build_function_tools(pkgs, allowed=allowed)}
        assert catalog == executable, allowed


def test_a_complex_schema_reaches_the_model_verbatim_when_loaded():
    """Moved from `test_tool_prompt.py`, where it guarded the same thing in the
    place the schema used to be rendered: an enum constraint, a nested object
    and a default must survive to the model, not be flattened by something
    being clever on the way. The schema now travels in the load reply, so the
    guard lives here.
    """
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": ["alloy-batches", "sensor-telemetry"]},
            "rows": {"type": "integer", "default": 25000},
            "nested": {"type": "object", "properties": {"deep": {"type": "boolean"}}},
        },
        "required": ["name"],
    }
    pkg = dataclasses.replace(
        _pkg(), commands=(CommandInfo("data-fetch", "Fetch a dataset.", schema),)
    )

    reply = load_reply(pkg, pkg.commands[0])

    start = reply.index("{")
    assert json.loads(reply[start : reply.rindex("}") + 1]) == schema


# ── round 1 A9 / A10 / A16: what the trim must NOT do ───────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # An abbreviation is not the end of a sentence. Measured before the fix:
        # each of these became the fragment on the right of the arrow.
        ("Max. 1000 rows per call. Fetches wafer history for a lot.", "Max. 1000 rows per call."),
        (
            "Plot a chart, e.g. a trend over time. Pass the csv path and a column.",
            "Plot a chart, e.g. a trend over time.",
        ),
        (
            "Normalise a wafer id, i.e. strip the lot prefix. Returns the bare id.",
            "Normalise a wafer id, i.e. strip the lot prefix.",
        ),
        (
            "Fetch SPC, CPK, yield etc. for one lot. Needs a lot id.",
            "Fetch SPC, CPK, yield etc. for one lot.",
        ),
        (
            "Compare lot A vs. lot B on every parameter. Prints a table.",
            "Compare lot A vs. lot B on every parameter.",
        ),
        ("Runs the U.S. spec check. Takes a lot id.", "Runs the U.S. spec check."),
        # Only the abbreviation rule catches these two: what follows is
        # capitalised, so the "next word starts a sentence" rule lets them
        # through. Deleting the abbreviation check leaves these red and
        # everything else green — which is how we know it is not dead code.
        (
            "Compare lot A vs. Lot B on every parameter. Prints a table.",
            "Compare lot A vs. Lot B on every parameter.",
        ),
        ("Draws a chart, e.g. Pareto by step. Takes a csv.", "Draws a chart, e.g. Pareto by step."),
        # …and a decimal never did (the lookahead already covered it).
        ("Keeps 3.5 digits of precision. Nothing else.", "Keeps 3.5 digits of precision."),
        # A real two-sentence description still stops at the first.
        ("Fetch a lot's history. Accepts a date range.", "Fetch a lot's history."),
    ],
)
def test_an_abbreviation_does_not_end_the_sentence(text, expected):
    assert catalog_description(text) == expected


def test_a_boundary_too_early_is_not_worse_than_no_boundary_at_all():
    """The last-resort cut keeps 99 characters; a single early boundary used to
    keep 5. The trim must never do worse than its own fallback."""
    long_tail = "Plot " + "x" * 200 + ". second sentence."
    out = catalog_description(long_tail)

    assert len(out) > 50, out
    assert len(out) <= 100


def test_a_tiny_or_silly_limit_does_not_invert_the_cut():
    # `first[: limit - 1]` made `limit=0` keep almost everything and `limit=-5`
    # drop five characters — the opposite of a limit.
    text = "Fetch a lot's history including every step and every tool it visited"
    for limit in (0, 1, -5):
        assert len(catalog_description(text, limit=limit)) <= 2, limit


def test_the_cut_does_not_land_inside_a_grapheme_cluster():
    """An emoji family is one thing to a reader; half of one is mojibake. The
    fuzz found 916 cuts ending on a combining mark or a dangling ZWJ."""
    out = catalog_description("A" * 95 + " 👨‍👩‍👧‍👦 and more text after it")

    assert "\u200d" not in out[-2:], repr(out)
    out2 = catalog_description("B" * 97 + "e\u0301 trailing")
    assert "\u0301" not in out2[-2:], repr(out2)


def test_a_description_that_is_not_a_string_does_not_take_the_tool_list_down():
    """A published manifest may carry `null`, a number or a list as a command
    description (`parse_manifest` checks neither), and this function is now on
    the path that builds the WHOLE tool list — so an odd value used to be an
    ugly f-string and would now fail every turn on that item (round 1 A8)."""
    for odd in (None, 42, ["a", "b"], {"x": 1}):
        assert isinstance(catalog_description(odd), str)  # ty: ignore[invalid-argument-type]

    # A description a published manifest can really carry, which the type says
    # cannot happen — the point of the test is that the code survives it.
    odd_cmd = CommandInfo("data-fetch", None, {"type": "object"})  # ty: ignore[invalid-argument-type]
    pkg = dataclasses.replace(_pkg(), commands=(odd_cmd,))
    (tool,) = build_catalog_tools([pkg], allowed=None, mark_loaded=lambda *_: None)

    assert tool.name == "data-fetch"


async def test_load_tool_bounds_its_reply_and_asks_for_the_same_thing_once():
    """The catalog's whole point is that schemas are not in the window until
    they are needed. One call naming every bundle put 36,054 characters back,
    and `["x"] * 20` put 261,238 — past the built-ins' own 200,000 ceiling,
    which does not reach these tools (round 1 A7)."""
    from workspace_app.tooling.registry import build_load_tool

    big = {
        "type": "object",
        "properties": {f"p{i}": {"type": "string", "description": "d" * 200} for i in range(40)},
    }
    pkg = dataclasses.replace(
        _pkg(),
        commands=tuple(CommandInfo(f"cmd-{i}", "Does a thing.", big) for i in range(12)),
    )
    load = build_load_tool([pkg], allowed=None, mark_loaded=lambda *_: None)
    ctx = AgentToolContext(investigation_id="i")

    # The same name twenty times is one load, not twenty.
    once = await load.on_invoke_tool(
        _tool_ctx(ctx, "load_tool"), json.dumps({"names": ["cmd-0"] * 20})
    )
    assert once.count("Call `cmd-0` again") == 1

    # A bundle with twelve fat commands is cut, and says so.
    whole = await load.on_invoke_tool(
        _tool_ctx(ctx, "load_tool"), json.dumps({"names": ["rca-tools"]})
    )
    assert len(whole) < 25_000, len(whole)
    assert "cut at" in whole

    # More names than one call may ask for: the surplus is named, not loaded.
    many = await load.on_invoke_tool(
        _tool_ctx(ctx, "load_tool"),
        # 27 DISTINCT names — the cap is on distinct names, so repeats of one
        # name (deduped above) would never reach it.
        json.dumps({"names": [f"cmd-{i}" for i in range(12)] + [f"nope-{i}" for i in range(15)]}),
    )
    assert "too many names" in many


def _tool_ctx(ctx, name: str, *, call_id: str = "id"):
    from agents.tool_context import ToolContext
    from agents.usage import Usage

    return ToolContext(
        context=ctx, tool_name=name, tool_call_id=call_id, tool_arguments="{}", usage=Usage()
    )

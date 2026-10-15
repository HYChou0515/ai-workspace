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
    assert fetch.description == "Fetch a lot's history."
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

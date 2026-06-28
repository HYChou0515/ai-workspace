"""The designed-pptx build loop (#284) — unit-tested end to end with a scripted
multimodal model + fake sandbox IO. No real node/soffice/model needed: the loop
is dependency-injected so every branch (generate / build-error / render-error /
no-slides / review-pass / out-of-budget / never-ok) is exercised here."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from workspace_app.agent.deck import loop as L
from workspace_app.agent.deck.loop import (
    CraftAssets,
    DeckIO,
    DeckRequest,
    build_deck,
)
from workspace_app.kb.vlm import IVlm

CODE = "```js\nconsole.log('deck');\n```"
ASSETS = CraftAssets(
    system_prompt="SYS", render_script="RENDER", theme_js="THEME", recipes_js="RECIPES"
)


class ScriptedVlm(IVlm):
    """Pops one reply per call; records (prompt, images). Emits a reasoning
    chunk first so the progress relay is exercised."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, object]] = []

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield "thinking ", True
        yield self.replies.pop(0), False


def make_io(vlm: IVlm, run):
    ws: dict[str, object] = {}

    async def write_text(p: str, c: str) -> None:
        ws[p] = c

    async def read_bytes(p: str) -> bytes:
        v = ws[p]
        return v if isinstance(v, bytes) else str(v).encode()

    async def list_dir(prefix: str) -> list[str]:
        return list(ws.keys())

    async def _run(cmd: list[str]) -> tuple[int, str]:
        return await run(cmd, ws)

    return DeckIO(
        vlm=vlm, write_text=write_text, read_bytes=read_bytes, list_dir=list_dir, run=_run
    ), ws


def render_ok(n: int = 2):
    async def run(cmd, ws):
        if cmd[0] == "node":
            return 0, ""
        for i in range(1, n + 1):
            ws[f"slide-{i}.jpg"] = f"jpg{i}".encode()
        return 0, "rendered"

    return run


async def _build(vlm, run, **kw):
    io, ws = make_io(vlm, run)
    res = await build_deck(DeckRequest(goal="G"), io, ASSETS, **kw)
    return res, io, ws


# ─── loop flows ────────────────────────────────────────────────────────


async def test_happy_review_pass():
    vlm = ScriptedVlm([CODE, "DECK_OK"])
    seen: list[str] = []
    res, io, ws = await _build(vlm, render_ok(2), progress=seen.append)

    assert res.ok and res.passed and res.rounds == 2
    assert res.out_path == "./deck.pptx"
    assert "reviewed and clean" in res.summary
    # first call = generate (no images); second = review (the 2 rendered slides).
    assert io.vlm.calls[0]["images"] == []  # type: ignore[index]
    assert io.vlm.calls[1]["images"] == [(b"jpg1", "image/jpeg"), (b"jpg2", "image/jpeg")]  # type: ignore[index]
    assert ws["build.js"] == "console.log('deck');\n"  # fence stripped, newline added
    assert ws["render_deck.sh"] == "RENDER" and ws["theme.js"] == "THEME"
    assert ws["recipes.js"] == "RECIPES"  # the craft library is written for build.js to require
    assert "thinking " in seen  # reasoning relayed to progress


async def test_out_path_in_subdir_lists_that_dir():
    vlm = ScriptedVlm([CODE, "DECK_OK"])
    io, _ws = make_io(vlm, render_ok())
    res = await build_deck(DeckRequest(goal="G", out_path="decks/d.pptx"), io, ASSETS)
    assert res.passed and res.out_path == "decks/d.pptx"


async def test_out_of_budget_keeps_last_deck():
    vlm = ScriptedVlm([CODE, CODE, CODE])
    res, _io, _ws = await _build(vlm, render_ok(), max_rounds=3)
    assert res.ok and not res.passed and res.rounds == 3
    assert "built in 3 round(s)" in res.summary


async def test_build_error_then_recover():
    calls = {"node": 0}

    async def run(cmd, ws):
        if cmd[0] == "node":
            calls["node"] += 1
            if calls["node"] == 1:
                return 1, "SyntaxError: boom"
            return 0, ""
        ws["slide-1.jpg"] = b"j1"
        return 0, "ok"

    vlm = ScriptedVlm(["bad code", CODE, "DECK_OK"])
    res, io, _ws = await _build(vlm, run, max_rounds=4)
    assert res.ok and res.passed and res.rounds == 3
    fix_prompt = io.vlm.calls[1]["prompt"]
    assert "failed to run" in fix_prompt and "SyntaxError: boom" in fix_prompt  # type: ignore[operator]
    assert io.vlm.calls[1]["images"] == []  # type: ignore[index]


async def test_never_ok_all_builds_fail():
    async def run(cmd, ws):
        return 1, "always broken"

    vlm = ScriptedVlm(["x", "x", "x", "x"])
    res, _io, _ws = await _build(vlm, run, max_rounds=4)
    assert not res.ok and not res.passed and res.rounds == 4
    assert res.summary.startswith("Could not build") and "always broken" in res.summary


async def test_render_failure_then_fix():
    calls = {"bash": 0}

    async def run(cmd, ws):
        if cmd[0] == "node":
            return 0, ""
        calls["bash"] += 1
        if calls["bash"] == 1:
            return 1, "soffice exploded"
        ws["slide-1.jpg"] = b"j1"
        return 0, "ok"

    vlm = ScriptedVlm([CODE, CODE, "DECK_OK"])
    res, io, _ws = await _build(vlm, run, max_rounds=4)
    assert res.ok and res.passed and res.rounds == 3
    assert "soffice exploded" in io.vlm.calls[1]["prompt"]  # type: ignore[operator]


async def test_render_no_slides_then_fix():
    calls = {"bash": 0}

    async def run(cmd, ws):
        if cmd[0] == "node":
            return 0, ""
        calls["bash"] += 1
        if calls["bash"] == 1:
            return 0, "rendered nothing"  # exit 0 but produced no slide-*.jpg
        ws["slide-1.jpg"] = b"j1"
        return 0, "ok"

    vlm = ScriptedVlm([CODE, CODE, "DECK_OK"])
    res, io, _ws = await _build(vlm, run, max_rounds=4)
    assert res.ok and res.passed and res.rounds == 3
    assert "no slide images" in io.vlm.calls[1]["prompt"]  # type: ignore[operator]


async def test_progress_none_is_safe():
    vlm = ScriptedVlm([CODE, "DECK_OK"])
    io, _ws = make_io(vlm, render_ok())
    res = await build_deck(DeckRequest(goal="G"), io, ASSETS, progress=None)
    assert res.passed


async def test_slides_read_in_numeric_order():
    async def run(cmd, ws):
        if cmd[0] == "node":
            return 0, ""
        for i in (10, 2, 1):  # insertion order ≠ numeric order
            ws[f"slide-{i}.jpg"] = f"j{i}".encode()
        return 0, "ok"

    vlm = ScriptedVlm([CODE, "DECK_OK"])
    res, io, _ws = await _build(vlm, run)
    assert res.passed
    imgs = io.vlm.calls[1]["images"]
    assert imgs == [(b"j1", "image/jpeg"), (b"j2", "image/jpeg"), (b"j10", "image/jpeg")]  # type: ignore[comparison-overlap]


# ─── pure helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("```js\nA();\n```", "A();\n"),
        ("```javascript\nB();\n```", "B();\n"),
        ("```\nC();\n```", "C();\n"),
        ("bare();", "bare();\n"),
    ],
)
def test_extract_code(reply, expected):
    assert L._extract_code(reply) == expected


@pytest.mark.parametrize(
    "reply,is_pass",
    [
        ("DECK_OK", True),
        ("deck_ok\n", True),
        ("DECK_OK but here ```js\nx\n```", False),  # has a code fence → not a pass
        ("Looks good", False),
    ],
)
def test_is_pass(reply, is_pass):
    assert L._is_pass(reply) is is_pass


def test_slide_sort_key_handles_missing_digits():
    assert L._slide_sort_key("slide-7.jpg") == 7
    assert L._slide_sort_key("weird.jpg") == 0


def test_intent_block_full_and_minimal():
    full = L._intent_block(
        DeckRequest(
            goal="G",
            audience="execs",
            length="one-pager",
            style="brand",
            notes="be terse",
            source_texts=[("report.md", "BODY")],
            source_images=[(b"x", "image/png")],
        )
    )
    for token in (
        "GOAL: G",
        "AUDIENCE: execs",
        "LENGTH: one-pager",
        "STYLE: brand",
        "NOTES: be terse",
    ):
        assert token in full
    assert "report.md" in full and "BODY" in full and "1 source image" in full

    minimal = L._intent_block(DeckRequest(goal="only"))
    assert minimal == "GOAL: only"


def test_prompt_builders():
    req = DeckRequest(goal="G", out_path="./out.pptx")
    gen = L._generate_prompt(req, ASSETS)
    assert "SYS" in gen and "GOAL: G" in gen and "./out.pptx" in gen
    fix = L._fix_prompt(req, "ERR")
    assert "failed to run" in fix and "ERR" in fix and "./out.pptx" in fix
    rev = L._review_prompt(req)
    assert "DECK_OK" in rev and "./out.pptx" in rev


def test_tail_and_one_line():
    assert L._tail("abc") == "abc"
    assert L._tail("x" * 5000).startswith("…") and len(L._tail("x" * 5000)) == 4001
    assert L._one_line("a   b\nc") == "a b c"
    assert L._one_line("y" * 200).endswith("…") and len(L._one_line("y" * 200)) == 81


def test_craft_assets_load():
    a = CraftAssets.load()
    assert "pptxgenjs" in a.system_prompt
    assert "soffice" in a.render_script
    assert "module.exports" in a.theme_js
    assert "module.exports" in a.recipes_js  # the require-able craft library

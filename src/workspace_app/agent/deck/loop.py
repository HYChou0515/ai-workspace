"""The designed-pptx build loop (issue #284).

A bounded **generate → render → see → fix** loop driven by one multimodal
model (an ``IVlm``). The model writes a bespoke ``pptxgenjs`` program; we run
it in the sandbox to get a ``.pptx``; we render that to per-slide images and
hand them *back* to the same model so it can *see* its own output and correct
layout bugs (overflow, overlap, clipping) that are invisible from code alone.

The loop is deliberately **dependency-injected** (`DeckIO` = a bundle of async
seams + an ``IVlm``): the API-side tool adapts a live sandbox + model into it,
while unit tests pass fakes. No real sandbox or model is needed to exercise
every branch — the trust boundary (`#284` decision 甲: code runs in the sandbox,
the model/loop stays API-side) lives in the *adapter*, not here.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ...kb.vlm import IVlm

# Workspace-relative scratch files the loop writes. `build.js` is the model's
# program; the rest are fixed craft assets it may `require`.
BUILD_JS = "build.js"
RENDER_SH = "render_deck.sh"
THEME_JS = "theme.js"
RECIPES_JS = "recipes.js"
# Per-slide render prefix — `render_deck.sh` emits `<dir>/slide-<n>.jpg`.
SLIDE_PREFIX = "slide-"
SLIDE_SUFFIX = ".jpg"
SLIDE_MIME = "image/jpeg"
# The review reply that means "every slide is correct — keep the current deck".
PASS_SENTINEL = "DECK_OK"

ProgressFn = Callable[[str], None]


@dataclass(frozen=True)
class CraftAssets:
    """The portable craft content (issue #284, P5). Kept separate from the loop
    logic so unit tests inject tiny stand-ins while the tool loads the real
    (large) package data via :meth:`load`."""

    system_prompt: str
    render_script: str
    theme_js: str
    recipes_js: str

    @classmethod
    def load(cls) -> CraftAssets:
        """Read the bundled craft assets from this package's ``assets/`` dir."""
        from importlib import resources

        base = resources.files(__package__) / "assets"
        return cls(
            system_prompt=(base / "system.md").read_text(encoding="utf-8"),
            render_script=(base / "render_deck.sh").read_text(encoding="utf-8"),
            theme_js=(base / "theme.js").read_text(encoding="utf-8"),
            recipes_js=(base / "recipes.js").read_text(encoding="utf-8"),
        )


@dataclass
class DeckRequest:
    """High-level intent the main agent hands `make_deck`. The loop — not the
    main agent — owns structure, layout and visual craft."""

    goal: str
    audience: str | None = None
    notes: str | None = None
    style: str | None = None
    length: str | None = None
    out_path: str = "./deck.pptx"
    # Source material gathered by the tool from the workspace: text files inlined
    # into the prompt, images shown to the multimodal model directly.
    source_texts: list[tuple[str, str]] = field(default_factory=list)
    source_images: list[tuple[bytes, str]] = field(default_factory=list)


@dataclass
class DeckIO:
    """The seams the loop drives. The tool wires these to a live sandbox +
    model; tests pass fakes.

    - ``vlm`` — the multimodal model (sees images, writes pptxgenjs).
    - ``write_text`` / ``read_bytes`` / ``list_dir`` — workspace file IO (DATA;
      no code execution).
    - ``run`` — execute a command **in the sandbox**, returning
      ``(exit_code, combined_output)``.
    """

    vlm: IVlm
    write_text: Callable[[str, str], Awaitable[None]]
    read_bytes: Callable[[str], Awaitable[bytes]]
    list_dir: Callable[[str], Awaitable[list[str]]]
    run: Callable[[list[str]], Awaitable[tuple[int, str]]]


@dataclass(frozen=True)
class DeckResult:
    out_path: str
    rounds: int
    passed: bool  # the model reviewed the render and declared it clean
    ok: bool  # at least one round produced a usable .pptx
    summary: str


async def build_deck(
    req: DeckRequest,
    io: DeckIO,
    assets: CraftAssets,
    *,
    max_rounds: int = 4,
    progress: ProgressFn | None = None,
) -> DeckResult:
    """Run the bounded build loop and return the outcome. Always leaves the most
    recent successfully-rendered deck at ``req.out_path`` (never empty-handed
    once any round builds); ``ok=False`` only when *no* round produced a deck."""
    _emit(progress, f"Planning deck — {_one_line(req.goal)}")
    # Fixed craft assets the model's program may `require`. Written once.
    await io.write_text(RENDER_SH, assets.render_script)
    await io.write_text(THEME_JS, assets.theme_js)
    await io.write_text(RECIPES_JS, assets.recipes_js)

    # The render script drops slides next to the .pptx. List that dir — "" for a
    # root-level deck (`./deck.pptx`), so the workspace facade lists the root.
    slide_dir = posixpath.dirname(req.out_path)
    if slide_dir in (".", ""):
        slide_dir = ""
    ok = False
    passed = False
    rounds = 0
    last_error = ""
    feedback: _Feedback = None

    for rnd in range(max_rounds):
        rounds = rnd + 1
        reply = await _ask_model(req, assets, io, feedback, progress)
        if feedback is not None and feedback[0] == "review" and _is_pass(reply):
            passed = True
            _emit(progress, "Review passed — slides look clean")
            break

        await io.write_text(BUILD_JS, _extract_code(reply))
        _emit(progress, f"Round {rounds}: building deck")
        code, out = await io.run(["node", BUILD_JS])
        if code != 0:
            last_error = out
            feedback = ("fix", _tail(out))
            _emit(progress, f"Round {rounds}: build failed, fixing")
            continue

        _emit(progress, f"Round {rounds}: rendering slides")
        rcode, rout = await io.run(["bash", RENDER_SH, req.out_path])
        if rcode != 0:
            last_error = rout
            feedback = ("fix", _tail(rout))
            _emit(progress, f"Round {rounds}: render failed, fixing")
            continue

        slides = await _read_slides(io, slide_dir)
        if not slides:
            last_error = "render produced no slide images"
            feedback = ("fix", last_error)
            continue

        ok = True
        if rnd == max_rounds - 1:
            # Out of budget to review/fix further — keep this deck.
            break
        feedback = ("review", slides)

    summary = _summarize(req, rounds, ok, passed, last_error)
    return DeckResult(out_path=req.out_path, rounds=rounds, passed=passed, ok=ok, summary=summary)


# ─── prompt construction ───────────────────────────────────────────────

# `_Feedback` drives which prompt the model gets next:
#   None              → first generation
#   ("fix", text)     → previous node/render run failed; fix the error
#   ("review", imgs)  → previous deck rendered; review the slide images
_Feedback = tuple[str, str] | tuple[str, list[tuple[bytes, str]]] | None


async def _ask_model(
    req: DeckRequest,
    assets: CraftAssets,
    io: DeckIO,
    feedback: _Feedback,
    progress: ProgressFn | None,
) -> str:
    """One multimodal call. Streams off the event loop so it doesn't block;
    forwards the model's reasoning to ``progress`` for live thinking."""
    if feedback is None:
        prompt = _generate_prompt(req, assets)
        images = list(req.source_images)
    elif feedback[0] == "fix":
        assert isinstance(feedback[1], str)
        prompt = _fix_prompt(req, feedback[1])
        images = []
    else:  # review
        assert isinstance(feedback[1], list)
        prompt = _review_prompt(req)
        images = feedback[1]

    def _on_chunk(text: str, is_reasoning: bool) -> None:
        if is_reasoning and text.strip():
            _emit(progress, text)

    return await asyncio.to_thread(io.vlm.collect, prompt, images=images, on_chunk=_on_chunk)


def _intent_block(req: DeckRequest) -> str:
    lines = [f"GOAL: {req.goal}"]
    if req.audience:
        lines.append(f"AUDIENCE: {req.audience}")
    if req.length:
        lines.append(f"LENGTH: {req.length}")
    if req.style:
        lines.append(f"STYLE: {req.style}")
    if req.notes:
        lines.append(f"NOTES: {req.notes}")
    if req.source_texts:
        lines.append("\nSOURCE MATERIAL (use as the deck's substance):")
        for path, text in req.source_texts:
            lines.append(f"\n----- {path} -----\n{text}")
    if req.source_images:
        lines.append(
            f"\n({len(req.source_images)} source image(s) are attached — use them "
            "as charts/figures or visual reference.)"
        )
    return "\n".join(lines)


def _output_contract(req: DeckRequest) -> str:
    return (
        f"Your program MUST `require('pptxgenjs')` and write the deck to "
        f"`{req.out_path}` via `pptx.writeFile({{ fileName: '{req.out_path}' }})`. "
        "Reply with the COMPLETE build.js inside one ```js code block and nothing else."
    )


def _generate_prompt(req: DeckRequest, assets: CraftAssets) -> str:
    return (
        f"{assets.system_prompt}\n\n"
        f"=== This deck ===\n{_intent_block(req)}\n\n"
        f"=== Output ===\n{_output_contract(req)}"
    )


def _fix_prompt(req: DeckRequest, error: str) -> str:
    return (
        "Your previous build.js failed to run. Fix it.\n\n"
        f"=== Error ===\n{error}\n\n"
        f"=== Output ===\n{_output_contract(req)}"
    )


def _review_prompt(req: DeckRequest) -> str:
    return (
        "Here are the rendered slides of your current deck, one image per slide. "
        "Inspect each for layout bugs: text overflowing its box, overlapping "
        "elements, text clipped at the top/bottom, labels colliding, content "
        "running under footers/page numbers.\n\n"
        f"If every slide is correct, reply with exactly `{PASS_SENTINEL}` and "
        "nothing else. Otherwise reply with the COMPLETE corrected build.js inside "
        f"one ```js code block. It must still write to `{req.out_path}`."
    )


# ─── reply parsing + small helpers ─────────────────────────────────────

_FENCE = re.compile(r"```(?:js|javascript)?\s*\n(.*?)```", re.DOTALL)


def _extract_code(reply: str) -> str:
    """The first fenced code block, or the whole reply when unfenced (a model
    that returned bare code)."""
    m = _FENCE.search(reply)
    return (m.group(1) if m else reply).strip() + "\n"


def _is_pass(reply: str) -> bool:
    return reply.strip().upper().startswith(PASS_SENTINEL) and "```" not in reply


async def _read_slides(io: DeckIO, slide_dir: str) -> list[tuple[bytes, str]]:
    """Read every rendered ``slide-<n>.jpg`` in numeric order."""
    names = [
        n
        for n in await io.list_dir(slide_dir)
        if posixpath.basename(n).startswith(SLIDE_PREFIX)
        and posixpath.basename(n).endswith(SLIDE_SUFFIX)
    ]
    names.sort(key=_slide_sort_key)
    out: list[tuple[bytes, str]] = []
    for name in names:
        out.append((await io.read_bytes(name), SLIDE_MIME))
    return out


def _slide_sort_key(name: str) -> int:
    """Numeric order from ``slide-7.jpg`` → 7 (pdftoppm may or may not zero-pad)."""
    digits = re.findall(r"\d+", posixpath.basename(name))
    return int(digits[-1]) if digits else 0


def _tail(text: str, limit: int = 4000) -> str:
    """Keep the end of an error blob — the traceback/last line matters most."""
    return text if len(text) <= limit else "…" + text[-limit:]


def _one_line(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _emit(progress: ProgressFn | None, text: str) -> None:
    if progress is not None:
        progress(text)


def _summarize(req: DeckRequest, rounds: int, ok: bool, passed: bool, last_error: str) -> str:
    if not ok:
        return (
            f"Could not build a deck after {rounds} round(s). Last error:\n"
            f"{_tail(last_error, 1500)}"
        )
    verdict = "reviewed and clean" if passed else f"built in {rounds} round(s)"
    return f"Wrote {req.out_path} ({verdict})."

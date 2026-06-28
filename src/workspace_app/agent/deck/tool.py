"""`make_deck` orchestration over plain async seams (issue #284).

Kept separate from `agent/tools.py` so the gather + run logic is unit-testable
with fake callables — the tool wrapper there only binds these seams to a live
sandbox + the resolved multimodal model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import magic

from ...filestore.protocol import FileNotFound
from ...kb.vlm import IVlm
from .loop import CraftAssets, DeckIO, DeckRequest, ProgressFn, build_deck

# Per-source text cap — a long report is summarised by the model, not pasted
# whole; this keeps the prompt bounded.
MAX_SOURCE_CHARS = 20_000
# Default build-loop budget (generate + up to N-1 review/fix rounds).
MAX_ROUNDS = 4

# Runtime tools the build loop needs IN THE SANDBOX (not the API): `node` runs
# the model's pptxgenjs program; `soffice`/`pdftoppm` render slides → images for
# the review pass. On the prod HTTP sandbox-host these live in the host image
# (see `sandbox-host/Dockerfile`); a host without them can't build decks, so we
# preflight rather than burn rounds + LLM calls on a guaranteed failure.
_PREFLIGHT_CMD = [
    "sh",
    "-lc",
    "command -v node >/dev/null 2>&1 "
    "&& command -v soffice >/dev/null 2>&1 "
    "&& command -v pdftoppm >/dev/null 2>&1",
]

ReadBytes = Callable[[str], Awaitable[bytes]]


async def toolchain_ready(exec_run: Callable[[list[str]], Awaitable[tuple[int, str]]]) -> bool:
    """True iff the sandbox has node + libreoffice + poppler — the deck loop's
    irreducible runtime tools."""
    code, _ = await exec_run(_PREFLIGHT_CMD)
    return code == 0


async def gather_sources(
    read_bytes: ReadBytes, paths: list[str], *, max_text_chars: int = MAX_SOURCE_CHARS
) -> tuple[list[tuple[str, str]], list[tuple[bytes, str]]]:
    """Split the agent's named source files into text (inlined into the prompt)
    and images (shown to the multimodal model). Missing paths are skipped — the
    deck is built from whatever material is actually present."""
    texts: list[tuple[str, str]] = []
    images: list[tuple[bytes, str]] = []
    for path in paths:
        try:
            data = await read_bytes(path)
        except FileNotFound:
            continue
        mime = magic.from_buffer(data, mime=True)
        if mime.startswith("image/"):
            images.append((data, mime))
        else:
            text = data.decode("utf-8", errors="replace")
            if len(text) > max_text_chars:
                text = text[:max_text_chars] + "\n…[truncated]"
            texts.append((path, text))
    return texts, images


async def run_make_deck(
    *,
    vlm: IVlm | None,
    write_text: Callable[[str, str], Awaitable[None]],
    read_bytes: ReadBytes,
    list_dir: Callable[[str], Awaitable[list[str]]],
    exec_run: Callable[[list[str]], Awaitable[tuple[int, str]]],
    progress: ProgressFn | None,
    goal: str,
    audience: str | None = None,
    source: list[str] | None = None,
    notes: str | None = None,
    style: str | None = None,
    length: str | None = None,
    out_path: str = "./deck.pptx",
    max_rounds: int = MAX_ROUNDS,
) -> str:
    """Gather sources, run the build loop, and return an LLM-facing result
    string (the deck path + verdict, or an `error: …` when no model is wired /
    no deck could be built)."""
    if vlm is None:
        return (
            "error: make_deck is unavailable — this deployment has no multimodal "
            "model configured for it (set kb.deck_vlm / kb.vlm_llm). Do not retry."
        )
    if not await toolchain_ready(exec_run):
        return (
            "error: make_deck is unavailable — this deployment's sandbox is missing "
            "the deck toolchain (node / libreoffice / poppler). Install it in the "
            "sandbox image (docker: see sandbox-host/Dockerfile). Do not retry."
        )
    source_texts, source_images = await gather_sources(read_bytes, source or [])
    io = DeckIO(
        vlm=vlm, write_text=write_text, read_bytes=read_bytes, list_dir=list_dir, run=exec_run
    )
    req = DeckRequest(
        goal=goal,
        audience=audience,
        notes=notes,
        style=style,
        length=length,
        out_path=out_path,
        source_texts=source_texts,
        source_images=source_images,
    )
    result = await build_deck(req, io, CraftAssets.load(), max_rounds=max_rounds, progress=progress)
    return result.summary if result.ok else f"error: {result.summary}"

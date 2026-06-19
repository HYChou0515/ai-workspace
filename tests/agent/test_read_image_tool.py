"""`read_image` tool (#112) — hand a workspace image to the VLM and return
its reply. Tested through the real `VlmDescriber` over a fake `IVlm`, so the
tool↔describer contract is exercised end-to-end."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, read_image_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.kb.vlm import IVlm, VlmDescriber

# A real 1×1 PNG — libmagic sniffs it as image/png.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class FakeVlm(IVlm):
    def __init__(self, chunks: list[tuple[str, bool]]) -> None:
        self.calls: list[dict[str, object]] = []
        self._chunks = chunks

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield from self._chunks


async def _ctx_with(vlm: IVlm | None, **kw) -> tuple[RunContextWrapper, WorkspaceFiles]:
    files = WorkspaceFiles(MemoryFileStore())
    describer = VlmDescriber(vlm) if vlm is not None else None
    ctx = RunContextWrapper(
        AgentToolContext(investigation_id="inv-1", files=files, describer=describer, **kw)
    )
    return ctx, files


async def test_read_image_with_question_returns_vlm_answer():
    vlm = FakeVlm([("the error is ", False), ("OOMKilled", False)])
    ctx, files = await _ctx_with(vlm)
    await files.write("inv-1", "/shot.png", _PNG)

    out = await read_image_impl(ctx, "/shot.png", question="what error is shown?")

    assert out == "the error is OOMKilled"
    (call,) = vlm.calls
    assert call["prompt"] == "what error is shown?"
    assert call["images"] == [(_PNG, "image/png")]


async def test_read_image_without_question_describes_the_whole_image():
    """No question → the full describe() path (layered OCR template)."""
    vlm = FakeVlm([("a bar chart of weekly yield", False)])
    ctx, files = await _ctx_with(vlm)
    await files.write("inv-1", "/chart.png", _PNG)

    out = await read_image_impl(ctx, "/chart.png")

    assert out == "a bar chart of weekly yield"
    # the describe template — not a bare question — was sent
    assert "verbatim" in str(vlm.calls[0]["prompt"]).lower()


async def test_read_image_reports_when_no_vlm_configured():
    """describer is None → a clear unavailable message, telling the agent not
    to retry, and the file is never even read."""
    ctx, files = await _ctx_with(None)
    await files.write("inv-1", "/shot.png", _PNG)

    out = await read_image_impl(ctx, "/shot.png", question="q")

    assert "not available" in out and "Do not retry" in out


async def test_read_image_rejects_a_non_image_file():
    """Magic-sniffed non-image bytes are rejected — the VLM is never called."""
    vlm = FakeVlm([("should not run", False)])
    ctx, files = await _ctx_with(vlm)
    await files.write("inv-1", "/notes.txt", b"just some plain text, not an image")

    out = await read_image_impl(ctx, "/notes.txt", question="q")

    assert out.startswith("error: not an image")
    assert vlm.calls == []


async def test_read_image_reports_a_missing_file():
    vlm = FakeVlm([("x", False)])
    ctx, _ = await _ctx_with(vlm)
    out = await read_image_impl(ctx, "/nope.png", question="q")
    assert out == "error: file not found: /nope.png"
    assert vlm.calls == []


async def test_read_image_streams_chunks_to_the_exec_sink():
    """The VLM stream is relayed live to on_exec_output (the tool card)."""
    vlm = FakeVlm([("thinking…", True), ("done", False)])
    seen: list[bytes] = []
    ctx, files = await _ctx_with(vlm, on_exec_output=seen.append)
    await files.write("inv-1", "/s.png", _PNG)

    await read_image_impl(ctx, "/s.png", question="q")

    assert seen == [b"thinking\xe2\x80\xa6", b"done"]


async def test_read_image_truncates_long_output_at_the_read_file_cap():
    vlm = FakeVlm([("x" * 500, False)])
    ctx, files = await _ctx_with(vlm, read_file_max_chars=100)
    await files.write("inv-1", "/big.png", _PNG)

    out = await read_image_impl(ctx, "/big.png", question="q")

    assert "x" * 500 not in out
    assert "omitted" in out

"""`make_deck` tool (#284): the `run_make_deck` orchestration over plain seams,
plus the `make_deck_impl` ctx wrapper end-to-end against a render-faking
sandbox (so the workspace read/list seams are exercised)."""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext
from workspace_app.agent.deck.tool import gather_sources, run_make_deck
from workspace_app.agent.tools import make_deck_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.kb.vlm import IVlm
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle, SandboxSpec
from workspace_app.sync import SandboxSync

# 1×1 transparent PNG — libmagic detects it as image/png.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC"
)
CODE = "```js\nconsole.log('x');\n```"


class ScriptedVlm(IVlm):
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, object]] = []

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        self.calls.append({"prompt": prompt, "images": list(images)})
        yield self.replies.pop(0), False


# ─── gather_sources ────────────────────────────────────────────────────


async def test_gather_sources_splits_text_images_and_skips_missing():
    from workspace_app.filestore.protocol import FileNotFound

    store = {"report.md": b"hello body", "chart.png": PNG}

    async def read_bytes(path: str) -> bytes:
        if path not in store:
            raise FileNotFound(path)
        return store[path]

    texts, images = await gather_sources(
        read_bytes, ["report.md", "chart.png", "missing.md"], max_text_chars=5
    )
    assert texts == [("report.md", "hello\n…[truncated]")]  # truncated at 5 chars
    assert images == [(PNG, "image/png")]

    # short text under the default cap is inlined whole (no truncation branch).
    texts2, _ = await gather_sources(read_bytes, ["report.md"])
    assert texts2 == [("report.md", "hello body")]


# ─── run_make_deck (fake seams) ────────────────────────────────────────


def _fake_seams(vlm, run):
    ws: dict[str, bytes] = {}

    async def write_text(p: str, c: str) -> None:
        ws[p] = c.encode()

    async def read_bytes(p: str) -> bytes:
        return ws[p]

    async def list_dir(prefix: str) -> list[str]:
        return list(ws)

    async def exec_run(cmd: list[str]) -> tuple[int, str]:
        return await run(cmd, ws)

    return dict(
        vlm=vlm, write_text=write_text, read_bytes=read_bytes, list_dir=list_dir, exec_run=exec_run
    )


async def test_run_make_deck_no_model_is_fail_loud():
    seams = _fake_seams(None, None)
    out = await run_make_deck(**seams, progress=None, goal="G")
    assert out.startswith("error:") and "no multimodal model" in out


async def test_run_make_deck_happy_returns_summary():
    async def run(cmd, ws):
        if cmd[0] == "node":
            return 0, ""
        ws["slide-1.jpg"] = b"j1"
        return 0, "ok"

    vlm = ScriptedVlm([CODE, "DECK_OK"])
    seams = _fake_seams(vlm, run)
    out = await run_make_deck(**seams, progress=None, goal="Make a deck", out_path="./deck.pptx")
    assert "deck.pptx" in out and "error:" not in out


async def test_run_make_deck_unbuildable_returns_error():
    async def run(cmd, ws):
        if cmd[0] == "sh":  # preflight passes — node exists
            return 0, ""
        return 1, "SyntaxError: boom"  # but every build.js fails

    vlm = ScriptedVlm(["x", "x"])
    seams = _fake_seams(vlm, run)
    out = await run_make_deck(**seams, progress=None, goal="G", max_rounds=2)
    assert out.startswith("error:") and "Could not build" in out


async def test_run_make_deck_missing_toolchain_fails_loud():
    """No node/soffice/poppler in the sandbox ⇒ a clear, actionable error
    returned BEFORE the loop — not silent failed rounds, and the model is never
    called."""

    async def run(cmd, ws):
        return 127, "sh: node: not found"  # preflight (and anything) fails

    vlm = ScriptedVlm(["x", "x", "x", "x"])
    seams = _fake_seams(vlm, run)
    out = await run_make_deck(**seams, progress=None, goal="G")
    assert out.startswith("error:")
    assert "toolchain" in out and "Do not retry" in out
    assert vlm.calls == []  # failed preflight ⇒ the model was never reached


# ─── make_deck_impl (ctx wrapper, real facade) ─────────────────────────


class _RenderSandbox(MockSandbox):
    """node → ok; the render step drops a slide image so the loop's read/list
    seams run; everything else falls back to MockSandbox."""

    async def exec(self, handle, cmd, on_output=None):  # type: ignore[override]
        if cmd and cmd[0] == "sh":  # preflight: toolchain present
            return ExecResult(exit_code=0, stdout=b"")
        if cmd and cmd[0] == "node":
            return ExecResult(exit_code=0, stdout=b"built\n")
        if cmd and cmd[0] == "bash":
            await self.upload(handle, PNG, "/slide-1.jpg")
            return ExecResult(exit_code=0, stdout=b"slide-1.jpg\n")
        return await super().exec(handle, cmd, on_output)


def _make_ctx(deck_vlm, sandbox=None, sink=None):
    spec = make_spec(default_user="test-user")
    sandbox = sandbox or MockSandbox()
    filestore = SpecstarFileStore(spec)
    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    holder: dict[str, SandboxHandle] = {}

    async def _resolve(ws: str) -> SandboxHandle | None:
        return holder.get(ws)

    files = WorkspaceFiles(filestore, sandbox, _resolve)

    async def wake(on_progress=None) -> SandboxHandle:
        h = await sandbox.create(SandboxSpec())
        await sync.restore("ws-test", h, on_progress=on_progress)
        holder["ws-test"] = h
        return h

    return RunContextWrapper(
        AgentToolContext(
            investigation_id="ws-test",
            sandbox=sandbox,
            filestore=filestore,
            files=files,
            sync=sync,
            ensure_sandbox_via=wake,
            deck_vlm=deck_vlm,
            on_exec_output=sink,
        )
    )


async def test_make_deck_impl_unavailable_without_model():
    ctx = _make_ctx(deck_vlm=None)
    out = await make_deck_impl(ctx, goal="anything")
    assert out.startswith("error:") and "unavailable" in out


async def test_make_deck_impl_builds_via_sandbox_and_streams_progress():
    chunks: list[bytes] = []
    vlm = ScriptedVlm([CODE, "DECK_OK"])
    ctx = _make_ctx(deck_vlm=vlm, sandbox=_RenderSandbox(), sink=chunks.append)
    out = await make_deck_impl(ctx, goal="Turn the report into a deck", out_path="./deck.pptx")
    assert "deck.pptx" in out and "error:" not in out
    # the review call saw the rendered slide image (read via the workspace facade)
    assert vlm.calls[1]["images"] == [(PNG, "image/jpeg")]
    assert chunks  # progress relayed to the exec-output sink


async def test_make_deck_impl_normalises_a_rooted_out_path():
    """`out_path` is not just a store key — it is interpolated into the JS the
    deck sub-agent writes (`pptx.writeFile({fileName: …})`) and passed to the
    render script, both of which run as real processes in the sandbox. A model
    that says `/deck.pptx` would write to the SYSTEM root, and the deck would
    then be missing from the workspace. Relative is the only form that means the
    same thing to node and to the file tools."""
    vlm = ScriptedVlm([CODE, "DECK_OK"])
    ctx = _make_ctx(deck_vlm=vlm, sandbox=_RenderSandbox(), sink=None)
    out = await make_deck_impl(ctx, goal="a deck", out_path="/deck.pptx")
    assert "error:" not in out
    # the path handed to the model in its own instructions carries no leading
    # slash (matched as a whole token — the sample code legitimately contains
    # "./deck.pptx", which merely *contains* the string "/deck.pptx")
    instructions = "\n".join(str(c["prompt"]) for c in vlm.calls)
    assert "`deck.pptx`" in instructions
    assert "`/deck.pptx`" not in instructions
    assert "fileName: 'deck.pptx'" in instructions

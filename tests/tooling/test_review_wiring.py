"""The #285 VLM-review wiring in the tool-package on_invoke path: gating,
stdout image extraction, and the loop folded into the tool result text."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from agents.tool_context import ToolContext
from agents.usage import Usage

from workspace_app.agent import AgentToolContext
from workspace_app.kb.vlm import IVlm, VlmDescriber
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import ExecResult, SandboxHandle
from workspace_app.tooling.registry import (
    CommandInfo,
    PackageInfo,
    _accepts_style,
    _images_from_stdout,
    _review_chart,
    _to_function_tool,
)

_HANDLE = SandboxHandle(id="h")


class _SeqVlm(IVlm):
    """Returns the next canned checklist answer per call (one chunk each)."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = answers

    def stream(
        self, prompt: str, *, images: Sequence[tuple[bytes, str]]
    ) -> Iterator[tuple[str, bool]]:
        yield (self._answers.pop(0), False)


def _ans(notes: str = "none", **flags: bool) -> str:
    keys = ["blank", "overlap", "truncated", "tiny_text", "clipped", "missing_legend"]
    return "\n".join(f"{k}: {'yes' if flags.get(k) else 'no'}" for k in keys) + f"\nnotes: {notes}"


class _StubSandbox(MockSandbox):
    """Canned exec results (one per re-render) + downloads that always succeed."""

    def __init__(self, exec_results: list[ExecResult]) -> None:
        super().__init__()
        self._exec_results = exec_results
        self.exec_calls: list[list[str]] = []

    async def exec(self, handle, cmd, on_output=None):  # noqa: ANN001
        self.exec_calls.append(cmd)
        r = self._exec_results.pop(0)
        if on_output is not None and r.stdout:
            on_output(r.stdout)
        return r

    async def download(self, handle, remote_path):  # noqa: ANN001
        return b"PNG:" + remote_path.encode()


class _NoDownloadSandbox(_StubSandbox):
    async def download(self, handle, remote_path):  # noqa: ANN001
        raise FileNotFoundError(remote_path)


_PKG = PackageInfo(name="sci-plot", commands=(), install_dir="../.tools/sci-plot")
_CMD = CommandInfo(name="chart", description="d", params_json_schema={})

_RESULT = ExecResult(exit_code=0, stdout=b'{"images": ["charts/c0.png"]}')


def _ctx(vlm_answers: list[str], sandbox: MockSandbox, sink=None) -> AgentToolContext:
    return AgentToolContext(
        investigation_id="inv",
        sandbox=sandbox,
        describer=VlmDescriber(_SeqVlm(vlm_answers)),
        on_exec_output=sink,
    )


# ─── _accepts_style ───────────────────────────────────────────────────


def test_accepts_style_top_level():
    assert _accepts_style({"properties": {"style": {}}})


def test_accepts_style_in_discriminated_union_defs():
    assert _accepts_style({"$defs": {"v": {"properties": {"style": {}, "chart": {}}}}})


def test_accepts_style_false():
    assert not _accepts_style({"properties": {"data": {}}, "$defs": {"v": {"properties": {}}}})


# ─── _images_from_stdout ──────────────────────────────────────────────


def test_images_from_stdout_images_key():
    assert _images_from_stdout(b'{"images": ["a.png", "b.png"]}') == ["a.png", "b.png"]


def test_images_from_stdout_plots_key():
    assert _images_from_stdout(b'{"plots": ["a.png"]}') == ["a.png"]


def test_images_from_stdout_handles_garbage():
    assert _images_from_stdout(b"not json") == []
    assert _images_from_stdout(b"[1,2,3]") == []  # not a dict
    assert _images_from_stdout(b'{"other": 1}') == []


# ─── _review_chart ────────────────────────────────────────────────────


async def test_review_no_images_returns_text_unchanged():
    actx = _ctx([], _StubSandbox([]))
    text = "Tool `chart` returned (exit_code=0):\n{}"
    out = await _review_chart(actx, _PKG, _CMD, _HANDLE, "{}", ExecResult(0, b"{}"), text)
    assert out == text


async def test_review_download_failure_skips():
    actx = _ctx([_ans(overlap=True)], _NoDownloadSandbox([]))
    text = "x charts/c0.png"
    out = await _review_chart(actx, _PKG, _CMD, _HANDLE, "{}", _RESULT, text)
    assert out == text  # couldn't read the png back → no review


async def test_review_improves_and_rewrites_best_path():
    captured: list[bytes] = []
    sandbox = _StubSandbox([ExecResult(0, b'{"images": ["charts/c1.png"]}')])
    actx = _ctx([_ans(overlap=True), _ans()], sandbox, sink=captured.append)
    text = 'Tool `chart` returned (exit_code=0):\n{"images": ["charts/c0.png"]}'
    out = await _review_chart(actx, _PKG, _CMD, _HANDLE, '{"chart": "box_scatter"}', _RESULT, text)
    assert "charts/c1.png" in out and "charts/c0.png" not in out  # best path swapped in
    assert "Visual check" in out
    assert captured  # the VLM stream was relayed to the sink


async def test_review_bad_args_and_no_image_rerender_keeps_first():
    # render's exec returns no image → path falls back to the first; no improvement.
    sandbox = _StubSandbox([ExecResult(0, b"{}")])
    actx = _ctx([_ans(overlap=True), _ans(overlap=True)], sandbox)
    text = 'x {"images": ["charts/c0.png"]}'
    out = await _review_chart(actx, _PKG, _CMD, _HANDLE, "not-json", _RESULT, text)
    assert "charts/c0.png" in out and "Visual check" in out  # first path kept


# ─── full FunctionTool invoke (on_invoke review gate) ─────────────────


async def test_on_invoke_runs_review_when_describer_and_style():
    cmd = CommandInfo(
        name="chart",
        description="d",
        params_json_schema={"$defs": {"v": {"properties": {"style": {}}}}},
    )
    sandbox = _StubSandbox(
        [
            ExecResult(0, b'{"images": ["charts/c0.png"]}'),  # first render
            ExecResult(0, b'{"images": ["charts/c1.png"]}'),  # the re-render
        ]
    )
    actx = _ctx([_ans(overlap=True), _ans()], sandbox)
    tool = _to_function_tool(_PKG, cmd)
    tctx: ToolContext[Any] = ToolContext(
        context=actx, tool_name="chart", tool_call_id="id", tool_arguments="{}", usage=Usage()
    )
    out = await tool.on_invoke_tool(tctx, '{"chart": "box_scatter"}')
    assert "Visual check" in out and "charts/c1.png" in out


async def test_on_invoke_skips_review_without_describer():
    sandbox = _StubSandbox([ExecResult(0, b'{"images": ["charts/c0.png"]}')])
    actx = AgentToolContext(investigation_id="inv", sandbox=sandbox, describer=None)
    tool = _to_function_tool(_PKG, _CMD)
    tctx: ToolContext[Any] = ToolContext(
        context=actx, tool_name="chart", tool_call_id="id", tool_arguments="{}", usage=Usage()
    )
    out = await tool.on_invoke_tool(tctx, "{}")
    assert "Visual check" not in out  # no VLM wired → render only, no review

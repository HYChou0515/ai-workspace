r"""`_format_exec` — what the LLM sees back from an `exec`-routed tool.

The output IS the tool's role="tool" message content. Qwen3:14b's chat
template maps role="tool" to a `<|im_start|>user` block wrapped in
`<tool_response>` tags (see Ollama's `ollama show qwen3:14b`'s
template branch), so the LLM reads tool output in a user-frame and
loses attribution when the content reads like a human narration. Two
defences:

1. Prefix every tool result with ``Tool `<name>` returned (exit_code=N):``
   so the model has an explicit anchor that this came from its own
   tool_call, not from the user.
2. Suppress stderr on success (exit_code == 0). The stderr stream
   already goes to the FE live via ``ctx.on_exec_output``; re-injecting
   it into the LLM's history adds 'materialising history for 9 wafers'
   / library deprecation warnings that look like user narration and
   misattribute. Keep stderr ONLY when the tool actually failed and
   the error explanation lives there.
"""

from __future__ import annotations

from workspace_app.agent.tools import _format_exec
from workspace_app.sandbox.protocol import ExecResult


def test_format_exec_prefixes_output_with_the_tool_name():
    """The model sees a `Tool <name> returned (exit_code=N):` first line,
    so even when qwen3's chat template renders the tool message in a
    user-frame, the content anchors back to the tool_call it came from."""
    r = ExecResult(exit_code=0, stdout=b'{"path": "wh.csv"}', stderr=b"")
    out = _format_exec("wafer-history", r)
    assert out.startswith("Tool `wafer-history` returned (exit_code=0):")


def test_format_exec_drops_stderr_on_success():
    """Production transcript (qwen3:14b): wafer-history's stderr noise
    ('materialising history for 9 wafers …', a joblib UserWarning)
    came back through the LLM's history alongside the JSON stdout.
    qwen3 read it as user narration ('The user ran the wafer-history
    command'). The fix: when exit_code == 0, drop stderr entirely —
    it already streamed to the FE live via `ctx.on_exec_output`, and
    a successful tool's stderr is by convention noise (progress logs,
    library warnings)."""
    r = ExecResult(
        exit_code=0,
        stdout=b'{"path": "wh.csv", "wafers": 9, "rows": 1105}',
        stderr=(
            b"materialising history for 9 wafers \xe2\x80\xa6\n"
            b"writing 1105 rows x 9 cols -> wh.csv \xe2\x80\xa6\n"
        ),
    )
    out = _format_exec("wafer-history", r)
    assert "materialising" not in out
    assert "writing 1105" not in out
    assert "stderr" not in out  # the framing label is gone too
    # The stdout payload is still there.
    assert '{"path": "wh.csv", "wafers": 9, "rows": 1105}' in out


def test_format_exec_keeps_stderr_on_failure_because_thats_where_the_error_lives():
    """A failed tool (exit_code != 0) typically writes its error to
    stderr (pydantic ValidationError, file-not-found, missing arg).
    Dropping stderr here would leave the model staring at empty
    stdout and no clue what broke — it can't retry intelligently."""
    r = ExecResult(
        exit_code=2,
        stdout=b"",
        stderr=b"1 validation error for Args\ncsv\n  Field required",
    )
    out = _format_exec("summarise", r)
    assert "exit_code=2" in out
    assert "1 validation error for Args" in out
    assert "Field required" in out


# ── display surface keeps success stderr (#62) ───────────────────────


def test_format_exec_keep_stderr_preserves_success_stderr_for_the_display_surface():
    """#62: the FE display surface is decoupled from the LLM-facing result.
    A command that exits 0 but wrote to stderr drops that stderr for the
    LLM (noise), but `keep_stderr=True` preserves it for display so the
    error the user saw stream live doesn't silently vanish from the final
    tool card, leaving a clean 'exit_code=0'."""
    r = ExecResult(exit_code=0, stdout=b"ok", stderr=b"ERROR: connection refused")
    cleaned = _format_exec("exec", r)
    display = _format_exec("exec", r, keep_stderr=True)
    assert "ERROR: connection refused" not in cleaned  # LLM-facing drops it
    assert "ERROR: connection refused" in display  # display keeps it
    assert "exit_code=0" in display


def test_format_exec_keep_stderr_is_noop_when_stderr_blank():
    """keep_stderr with no stderr → identical to the cleaned form (no
    dangling `--- stderr ---` header on a clean success)."""
    r = ExecResult(exit_code=0, stdout=b"ok", stderr=b"")
    assert _format_exec("exec", r, keep_stderr=True) == _format_exec("exec", r)


# ── output truncation (#44) ──────────────────────────────────────────


def test_format_exec_caps_huge_output_keeping_head_and_tail():
    """Issue #44: `grep`-ing a big file used to return everything,
    blowing up the model's context. Output over the cap is truncated
    head+tail (the start AND the end stay — grep matches at the top, the
    summary/count at the bottom) with an elision marker that tells the
    agent to narrow its command."""
    lines = "\n".join(f"line-{i:05d} match" for i in range(5000))
    r = ExecResult(exit_code=0, stdout=lines.encode(), stderr=b"")

    out = _format_exec("exec", r, max_chars=2000)

    assert len(out) < 2500  # capped (+ header + marker headroom)
    assert "line-00000 match" in out  # head kept
    assert "line-04999 match" in out  # tail kept
    assert "line-02500" not in out  # middle dropped
    assert "omitted" in out  # an elision marker is present
    assert "exit_code=0" in out  # the header always survives


def test_format_exec_no_cap_leaves_output_intact():
    """No cap configured (max_chars=None) → unchanged, so small outputs
    and the existing callers are unaffected."""
    r = ExecResult(exit_code=0, stdout=b"small output", stderr=b"")
    assert _format_exec("exec", r) == _format_exec("exec", r, max_chars=None)
    assert "small output" in _format_exec("exec", r, max_chars=10_000)


def test_format_exec_truncation_keeps_the_failure_stderr_at_the_tail():
    """On failure the error lives in stderr, appended last — head+tail
    truncation keeps the tail, so the model still sees WHY it broke even
    when stdout was huge."""
    noise = "\n".join(f"progress {i}" for i in range(5000))
    r = ExecResult(
        exit_code=1,
        stdout=noise.encode(),
        stderr=b"FATAL: out of memory at row 4999",
    )
    out = _format_exec("exec", r, max_chars=1500)
    assert "FATAL: out of memory" in out

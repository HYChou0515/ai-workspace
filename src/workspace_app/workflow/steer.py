"""The steerer (#288, manual §10) — translate a free-text instruction into a SteerPlan.

A read-only agent turn reads the run's current inputs + journal and proposes the
**minimal** change to honour the operator's ask: which input files to rewrite + which
steps to **invalidate** (delete the artifact → force re-run; downstream cascades via
input-hash, §9). It only PROPOSES (decision); ``apply_steer`` commits the edits
(action) — the §8 decision/action split, so a mis-steer can't touch the workspace until
a human approves. Parsing is tolerant (the model may fence the JSON or wrap it in prose)
with retry-with-feedback, mirroring the gate's in-step retry (§6).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ..files import rel_path
from .run import SteerInputEdit, SteerPlan

if TYPE_CHECKING:
    from .handle import WorkflowHandle

# The read-only tools the steerer may use to inspect the workspace (manual §10): it
# reads, it never writes — ``apply_steer`` does the writing.
READONLY_TOOLS = ["read_file", "list_files"]

# The journal home (#136) + everything under it is off-limits to input edits: the
# steerer rewrites *inputs* and invalidates steps by NAME, it never hand-edits the
# journal (that path is owned by the engine).
_JOURNAL_PREFIX = "/.workflow"

# Cap how much of each input file is inlined into the steerer's prompt, so a big input
# can't blow the context window — the model can still ``read_file`` the rest.
_INLINE_LIMIT = 8000

logger = logging.getLogger(__name__)


class SteerProposalFailed(Exception):
    """The steerer did not return a usable plan after its retries (#288)."""


def _under_journal(path: str) -> bool:
    norm = path if path.startswith("/") else "/" + path
    return norm == _JOURNAL_PREFIX or norm.startswith(_JOURNAL_PREFIX + "/")


def _extract_json(text: str) -> Any:
    """Pull a JSON object out of a model reply that may fence it in ``` blocks or wrap
    it in prose. Returns the parsed object, or None when nothing parses."""
    s = text.strip()
    if "```" in s:
        # Prefer the content of a fenced block (optionally tagged ``json``).
        for part in s.split("```"):
            p = part.strip()
            if p.startswith("json"):
                p = p[len("json") :].strip()
            if p.startswith("{"):
                s = p
                break
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end < start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _coerce_plan(obj: Any, instruction: str) -> tuple[SteerPlan | None, str]:
    """Validate a parsed object into a SteerPlan, returning ``(plan, error)``. A plan
    must carry at least one concrete move (an edit or an invalidation) — an empty
    proposal is treated as a failure and retried."""
    if not isinstance(obj, dict):
        return None, "the reply was not a JSON object"
    edits_raw, inval_raw = obj.get("input_edits", []), obj.get("invalidate", [])
    if not isinstance(edits_raw, list) or not isinstance(inval_raw, list):
        return None, "'input_edits' and 'invalidate' must both be lists"
    edits: list[SteerInputEdit] = []
    for e in edits_raw:
        if not isinstance(e, dict) or "path" not in e or "content" not in e:
            return None, "each input_edit needs a 'path' and a 'content'"
        path = str(e["path"])
        if _under_journal(path):
            return None, f"cannot edit the journal path {path!r}; invalidate the step by name"
        edits.append(SteerInputEdit(path=path, content=str(e["content"])))
    invalidate = [str(s) for s in inval_raw]
    if not edits and not invalidate:
        return None, "propose at least one input edit or one step to invalidate"
    return (
        SteerPlan(
            instruction=instruction,
            rationale=str(obj.get("rationale", "")),
            input_edits=edits,
            invalidate=invalidate,
        ),
        "",
    )


async def _inline(wf: WorkflowHandle, path: str) -> str:
    raw = await wf.read(path)
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        return f"### {path}\n(binary, {len(raw)} bytes — not shown)"
    if len(text) > _INLINE_LIMIT:
        text = text[:_INLINE_LIMIT] + "\n…(truncated; use read_file for the rest)"
    return f"### {path}\n{text}"


async def _journal_steps(wf: WorkflowHandle) -> list[str]:
    """The distinct journaled step names (``step_<name>/...`` under the run's journal
    home) — the set the steerer may invalidate."""
    names: list[str] = []
    for p in await wf.glob([f"{wf.journal_dir.lstrip('/')}/step_*/*"]):
        for seg in p.lstrip("/").split("/"):
            if seg.startswith("step_"):
                name = seg[len("step_") :]
                if name not in names:
                    names.append(name)
    return names


async def _workspace_context(wf: WorkflowHandle) -> str:
    """A compact, grounded view for the steerer: the editable input files (everything
    outside the journal, inlined) + the journaled step names it may invalidate."""
    files = await wf.glob(["*"], exclude=[f"{_JOURNAL_PREFIX.lstrip('/')}/*"])
    steps = await _journal_steps(wf)
    parts = ["Editable input files (rewrite the FULL new content for any you change):"]
    parts += [await _inline(wf, p) for p in files]
    parts.append("Completed steps you may invalidate by name: " + (", ".join(steps) or "(none)"))
    return "\n\n".join(parts)


def _steer_prompt(instruction: str, context: str, feedback: str | None) -> str:
    prompt = (
        "You are steering a paused workflow run. The operator wants to redirect it:\n"
        f'  "{instruction}"\n\n'
        "The run's current state:\n\n"
        f"{context}\n\n"
        "Propose the MINIMAL change that honours the request, as a single JSON object:\n"
        '  "rationale": one plain line on what you change and why,\n'
        '  "input_edits": a list of {"path", "content"} giving the FULL new content for\n'
        "      each input file you rewrite (only files listed above; never a journal path),\n"
        '  "invalidate": a list of step names to re-run (their cached result is deleted).\n'
        "Change as little as possible — do NOT invalidate steps the request doesn't affect, so\n"
        "valid expensive work is reused. Reply with ONLY the JSON object."
    )
    if feedback:
        prompt += f"\n\nYour previous reply was unusable: {feedback}\nReturn corrected JSON only."
    return prompt


async def propose_steer(
    wf: WorkflowHandle,
    *,
    instruction: str,
    tools: list[str] | None = None,
    retries: int = 2,
) -> SteerPlan:
    """Drive a read-only steerer turn and return its proposed ``SteerPlan`` (stamped
    with ``instruction``). Retries with the parse error fed back; raises
    ``SteerProposalFailed`` if no usable plan comes back."""
    if wf.drive_turn is None:
        raise RuntimeError("propose_steer needs a turn driver (wired by the run driver)")
    context = await _workspace_context(wf)
    error = ""
    feedback: str | None = None
    use_tools = tools or READONLY_TOOLS
    for _ in range(retries + 1):
        reply = await wf.drive_turn(_steer_prompt(instruction, context, feedback), use_tools)
        plan, error = _coerce_plan(_extract_json(str(reply)), instruction)
        if plan is not None:
            logger.info("steer: proposal accepted for instruction %r", instruction)
            return plan
        logger.warning("steer: proposal unusable: %s", error)
        feedback = error
    logger.warning("steer: no usable proposal for instruction %r after retries", instruction)
    raise SteerProposalFailed(error or "the steerer returned no usable plan")


async def _next_receipt_path(wf: WorkflowHandle) -> str:
    """The next ``steer/<seq>.json`` audit slot under the run's journal home, so each
    steer leaves its own receipt (a run's steer history is auditable)."""
    existing = await wf.glob([f"{wf.journal_dir.lstrip('/')}/steer/*.json"])
    return f"{wf.journal_dir}/steer/{len(existing) + 1:04d}.json"


async def apply_steer(wf: WorkflowHandle, plan: SteerPlan, *, decided_by: str = "") -> str:
    """Commit a confirmed ``SteerPlan`` (#288, manual §10): write its input edits,
    delete every artifact of each invalidated step (so §9 re-runs it), and journal an
    audit receipt. Returns the receipt path. A journal-path edit is refused — the engine
    stays the journal's only writer (``propose_steer`` already blocks this; re-checked
    here as defense-in-depth)."""
    applied: list[str] = []
    for edit in plan.input_edits:
        if _under_journal(edit.path):
            raise ValueError(f"refusing to write into the journal: {edit.path!r}")
        await wf.write(edit.path, edit.content)
        logger.debug("steer: wrote input edit %s", edit.path)
        applied.append(rel_path(edit.path))
    deleted: list[str] = []
    for name in plan.invalidate:
        for p in await wf.glob([f"{wf.journal_dir.lstrip('/')}/step_{name}/*"]):
            await wf.delete(p)
            logger.debug("steer: invalidated step %r artifact %s", name, p)
            deleted.append(p)
    receipt = await _next_receipt_path(wf)
    await wf.write_json(
        receipt,
        {
            "instruction": plan.instruction,
            "rationale": plan.rationale,
            "invalidate": list(plan.invalidate),
            "decided_by": decided_by,
            "applied": applied,
            "deleted": deleted,
        },
    )
    logger.info("steer: applied plan receipt=%s decided_by=%s", receipt, decided_by)
    return receipt

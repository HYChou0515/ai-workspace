"""``→consolidate`` workflow (topic-hub §12) — tidy the Hub's memory.

A single agent node reads the current memory (``MEMORY.md`` + the ``memory/*.md``
notes) and rewrites it: merge duplicates, summarise, and DROP anything stale or
superseded. Self-referential — last-write-wins on ``memory/`` (§3.1). Run-triggered
(a human or an external scheduler hits Run); there is no platform scheduler.

``inputs["context"]`` (optional) carries recent-chat excerpts the caller chooses to
fold in — the workflow library has no conversation access, so the App provides that
text rather than the run reaching into chats.

Loaded by file path (hyphenated slug) → absolute imports only.
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_step, file_nonempty
from workspace_app.workflow.handle import WorkflowHandle
from workspace_app.workflow.preflight import PreflightItem, PreflightReport


async def preflight(wf: WorkflowHandle, inputs: dict[str, Any]) -> PreflightReport:
    """#283 pre-flight: there must be existing memory to consolidate — block when the
    Hub has neither ``MEMORY.md`` nor any ``memory/*.md`` note (the run would do nothing)."""
    notes = await wf.glob(["memory/*.md"])
    has_memory = bool(notes) or await wf.exists("MEMORY.md")
    n = len(notes)
    return PreflightReport(
        summary=(
            f"重讀 MEMORY.md 與 {n} 則記憶筆記，再改寫它們——去重、合併、刪除過時內容。"
            if has_memory
            else "目前沒有任何記憶可整理。"
        ),
        checks=[
            PreflightItem(
                label="Hub 已有可整理的記憶",
                ok=has_memory,
                reason="" if has_memory else "先建立記憶（執行「消化上傳成記憶」）再來整理。",
            )
        ],
    )


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    notes = await wf.glob(["memory/*.md"])
    extra = inputs.get("context", "")
    context_line = (
        f"\n\nAlso weigh this recent context (e.g. recent chats):\n{extra}\n" if extra else ""
    )
    await agent_step(
        wf,
        phase="consolidate",
        name="consolidate",
        prompt=(
            f"Read the Hub's current memory: MEMORY.md and the notes {notes}.{context_line}\n"
            f"Consolidate it — merge duplicates, summarise verbosity, and DROP anything stale "
            f"or superseded. Rewrite MEMORY.md as the tightened, current index, and tidy the "
            f"notes where useful. These files already exist, so use edit_file to rewrite them "
            f"(read_file first), and delete_file for a note that is wholly obsolete. Output "
            f"nothing else."
        ),
        tools=["read_file", "write_file", "edit_file", "list_files", "delete_file"],
        check=file_nonempty("MEMORY.md"),
        retries=2,
    )
    return {"status": "done", "notes": len(notes)}

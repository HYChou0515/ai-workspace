"""``→memory`` workflow (topic-hub §12) — digest uploaded material into memory files.

Produce-then-write: an agent node reads + summarises each upload into ``memory/<f>.md``,
then a final agent node rewrites ``MEMORY.md`` as the current short index. Every node is
gated on a non-empty file it must write (manual §5.1 — an agent node is always gated);
re-run skips completed steps (the filesystem journal, §9), so a human can edit one note
and re-run only what changed.

Loaded by file path (the slug is hyphenated), so it uses absolute imports of the
workflow library — its globals stay valid after the path-exec load.
"""

from __future__ import annotations

from typing import Any

from workspace_app.workflow import agent_write_step
from workspace_app.workflow.handle import WorkflowHandle


def _slug(path: str) -> str:
    """A workspace path → a flat, extension-less stem for its memory note + step key."""
    base = path.lstrip("/").replace("/", "_")
    return base.rsplit(".", 1)[0] if "." in base else base


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    # #198: glob the profile's staging folder (``wf.upload_dir``), not a hardcoded
    # ``uploads/`` (#234) — so it stays in sync with where the chat attach lands.
    up = wf.upload_dir.rstrip("/")
    files = await wf.glob(
        inputs.get("files", [f"{up}/*"]),
        exclude=inputs.get("except", [f"{up}/input.json"]),
    )
    if not files:
        return {"status": "empty", "notes": 0}

    # Phase 1 — DIGEST: one memory note per upload. The agent REPLIES with the note
    # content (decision/action, #107) and the step writes it — no long write_file arg.
    notes: list[str] = []
    for f in files:
        out = f"memory/{_slug(f)}.md"
        await agent_write_step(
            wf,
            phase="digest",
            name=f"digest_{_slug(f)}",
            out=out,
            prompt=(
                f"Read the file {f}. Write a concise memory note capturing the key facts, "
                f"decisions, and open questions worth remembering long-term about this Topic "
                f"Hub's subject. Reply with ONLY the note as Markdown — no preamble, no code "
                f"fences. Your entire reply is saved verbatim as the note."
            ),
            tools=["read_file"],
            retries=2,
        )
        notes.append(out)

    # Phase 2 — INDEX: rewrite MEMORY.md (the always-in-context core) from the notes.
    # Same decision/action shape — the agent replies with the index, the step writes it.
    await agent_write_step(
        wf,
        phase="index",
        name="refresh_index",
        out="MEMORY.md",
        prompt=(
            f"The Hub's deeper memory notes are: {notes}. Write MEMORY.md as a short, current "
            f"index of what this Hub knows — a few bullets, each linking the relevant note. Keep "
            f"it tight; detail stays in the notes. Reply with ONLY the Markdown index content — "
            f"no preamble, no code fences. Your entire reply is saved verbatim as MEMORY.md."
        ),
        tools=["read_file", "list_files"],
        retries=2,
    )
    return {"status": "done", "notes": len(notes)}

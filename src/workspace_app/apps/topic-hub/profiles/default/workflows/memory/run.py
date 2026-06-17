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

from workspace_app.workflow import agent_step, file_nonempty
from workspace_app.workflow.handle import WorkflowHandle


def _slug(path: str) -> str:
    """A workspace path → a flat, extension-less stem for its memory note + step key."""
    base = path.lstrip("/").replace("/", "_")
    return base.rsplit(".", 1)[0] if "." in base else base


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    files = await wf.glob(
        inputs.get("files", ["inputs/*"]),
        exclude=inputs.get("except", ["inputs/input.json"]),
    )
    if not files:
        return {"status": "empty", "notes": 0}

    # Phase 1 — DIGEST: one memory note per upload (the agent must write it; gated).
    notes: list[str] = []
    for f in files:
        out = f"memory/{_slug(f)}.md"
        await agent_step(
            wf,
            phase="digest",
            name=f"digest_{_slug(f)}",
            prompt=(
                f"Read the file {f}. Write a concise memory note capturing the key facts, "
                f"decisions, and open questions worth remembering long-term about this Topic "
                f"Hub's subject. Save it as Markdown to {out} with write_file. Output nothing else."
            ),
            tools=["read_file", "write_file"],
            check=file_nonempty(out),
            retries=2,
        )
        notes.append(out)

    # Phase 2 — INDEX: rewrite MEMORY.md (the always-in-context core) from the notes.
    await agent_step(
        wf,
        phase="index",
        name="refresh_index",
        prompt=(
            f"The Hub's deeper memory notes are: {notes}. Rewrite MEMORY.md so it is a short, "
            f"current index of what this Hub knows — a few bullets, each linking the relevant "
            f"note. Keep it tight; detail stays in the notes. Save MEMORY.md with write_file. "
            f"Output nothing else."
        ),
        tools=["read_file", "write_file", "ls"],
        check=file_nonempty("MEMORY.md"),
        retries=2,
    )
    return {"status": "done", "notes": len(notes)}

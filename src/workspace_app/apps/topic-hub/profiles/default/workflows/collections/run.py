"""``→collections`` workflow (topic-hub §12) — the canonical produce → review → commit,
with the review *content* living in files.

PRODUCE: an agent node classifies each upload into one of the Hub's collections
(``collections.json``, §5), writes a digest, and collects the unknown domain terms a
newcomer wouldn't know → ``plan/<f>.json`` (gated by ``choice_in`` — the agent never
holds a side-effecting tool). An agent node then writes those terms into a fill-in
``glossary.todo.md``.

REVIEW: a simple yes/no ``human_gate``. The *questions* are in ``glossary.todo.md`` —
the human fills the definitions in the IDE (or opens a sibling chat to have the LLM
help; shared FileStore, §3.1), then approves.

COMMIT (deterministic, idempotent): ``ingest_to_collection`` files each upload and
``create_context_card`` (§8) authors a context card for each filled glossary entry.
Re-run replays completed steps (§9); nothing reaches a collection before approval.

Loaded by file path (hyphenated slug) → absolute imports only.
"""

from __future__ import annotations

from typing import Any

from workspace_app.filestore.protocol import FileNotFound
from workspace_app.workflow import (
    agent_step,
    choice_in,
    collection_has,
    file_nonempty,
    human_gate,
)
from workspace_app.workflow.handle import WorkflowHandle

_GLOSSARY = "glossary.todo.md"


def _plan_path(f: str) -> str:
    return "plan/" + f.lstrip("/").replace("/", "_") + ".json"


async def _read_collections(wf: WorkflowHandle) -> list[str]:
    """The Hub's collection NAMES (the agent picks among these; ingest / card-author
    resolve a name → id). Tolerant of an absent / malformed hand-edited file."""
    try:
        data = await wf.read_json("collections.json")
    except (FileNotFound, ValueError):
        return []
    out: list[str] = []
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("id")
                if isinstance(name, str) and name:
                    out.append(name)
    return out


def _parse_glossary(text: str) -> list[tuple[str, str]]:
    """Parse the human-filled ``glossary.todo.md`` into ``(term, definition)`` pairs —
    only ``## <term>`` sections whose body the human actually filled in (non-blank)."""
    entries: list[tuple[str, str]] = []
    term: str | None = None
    body: list[str] = []

    def _flush() -> None:
        if term is not None and (filled := "\n".join(body).strip()):
            entries.append((term, filled))

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            term, body = line[3:].strip(), []
        elif term is not None:
            body.append(line)
    _flush()
    return entries


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    collections = await _read_collections(wf)
    if not collections:
        return {"status": "no_collections"}
    files = await wf.glob(
        inputs.get("files", ["inputs/*"]),
        exclude=inputs.get("except", ["inputs/input.json"]),
    )
    if not files:
        return {"status": "empty", "files": 0}

    # Phase 1 — CLASSIFY: pick a collection + digest + collect unknown terms, as data.
    plan: dict[str, Any] = {}
    for f in files:
        out = _plan_path(f)
        await agent_step(
            wf,
            phase="classify",
            name="classify_" + f.lstrip("/").replace("/", "_"),
            prompt=(
                f"Read the file {f}. Choose the single best collection for it from "
                f"{collections}. Write a one-line digest. List the domain terms or "
                f"abbreviations in it that a newcomer would not know. Then write a JSON "
                f'object {{"collection": <one of {collections}>, "digest": <text>, '
                f'"terms": [<term>, ...]}} to {out} with write_file (use edit_file if {out} '
                f"already exists). Output nothing else."
            ),
            tools=["read_file", "write_file", "edit_file"],
            check=choice_in(out, key="collection", allowed=collections),
            retries=2,
        )
        plan[f] = await wf.read_json(out)

    # Phase 2 — GLOSSARY: collect the unknown terms into a fill-in file for a human.
    terms = sorted(
        {
            t.strip()
            for f in files
            for t in (plan[f].get("terms") or [])
            if isinstance(t, str) and t.strip()
        }
    )
    await agent_step(
        wf,
        phase="glossary",
        name="glossary",
        prompt=(
            f"These domain terms were collected while classifying: {terms}. Write a fill-in "
            f"glossary to {_GLOSSARY} — one '## <term>' section per term with an empty line "
            f"under it for a human to write the definition. Use write_file (or edit_file if "
            f"{_GLOSSARY} already exists). Output nothing else."
        ),
        tools=["read_file", "write_file", "edit_file"],
        check=file_nonempty(_GLOSSARY),
        retries=2,
    )

    # Phase 3 — REVIEW: a simple yes/no; the questions live in glossary.todo.md (§12).
    decision = await human_gate(
        wf,
        phase="review",
        title="Filled in the glossary? Continue to commit?",
        summary={f: plan[f].get("collection") for f in files},
        allow=["approve", "reject"],
    )
    if decision.choice == "reject":
        return {"status": "rejected", "files": len(files)}

    # Phase 4 — COMMIT: ingest each upload + author a card per filled glossary entry.
    term_collection: dict[str, str] = {}
    for f in files:
        coll = plan[f].get("collection")
        for t in plan[f].get("terms") or []:
            if isinstance(t, str) and t.strip():
                term_collection.setdefault(t.strip(), coll)

    ingested = 0
    for f in files:
        coll = plan[f]["collection"]
        await wf.ingest_to_collection(coll, f, phase="commit")
        if (await collection_has(coll, f)(wf, None)).ok:
            ingested += 1

    cards = 0
    for term, body in _parse_glossary(await wf.read_text(_GLOSSARY)):
        await wf.create_context_card(
            term_collection.get(term, collections[0]),
            [term],
            title=term,
            body=body,
            phase="commit",
        )
        cards += 1

    return {"status": "approved", "ingested": ingested, "cards": cards}

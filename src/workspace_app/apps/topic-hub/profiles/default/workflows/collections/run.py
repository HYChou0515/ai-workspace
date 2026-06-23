"""``→collections`` workflow (topic-hub §12, #133) — the canonical
produce → review → commit, with the review *content* living in files.

PRODUCE: an agent node classifies each upload into one of the Hub's collections
(``collections.json``, §5), writes a digest, and — while it still has the file open —
**drafts a short definition for each unknown domain term**, judging whether it is
confident the draft is right (``plan/r<n>/<f>.json``, gated by ``classify_plan`` so the
agent never holds a side-effecting tool). A deterministic node then assembles those
drafts into ``glossary.todo.md``: confident drafts become the definition; uncertain ones
become a ``⚠️`` line for the human to resolve.

REVIEW: a ``human_gate`` (approve / reject / **revise**). The *content* to review is the
drafted ``glossary.todo.md`` — the human reads/edits it in the IDE (shared FileStore,
§3.1), then **approves** (commit what's there, incl. their edits). **Revise** + feedback
re-runs the whole produce step to regenerate the drafts (overwriting). **Reject** ends
the run for interactive takeover.

COMMIT (deterministic, idempotent): ``ingest_to_collection`` files each upload and
``upsert_context_card`` (§8, #111) authors a context card for each *filled* glossary
entry — create-or-update by key, so re-classifying a term updates its card. Entries that
are still only a ``⚠️`` line are skipped (unresolved). Re-run replays completed steps
(§9); nothing reaches a collection before approval.

Loaded by file path (hyphenated slug) → absolute imports only.
"""

from __future__ import annotations

from typing import Any

from workspace_app.filestore.protocol import FileNotFound
from workspace_app.workflow import (
    agent_step,
    collection_has,
    human_gate,
)
from workspace_app.workflow.engine import CheckResult, run_step
from workspace_app.workflow.handle import WorkflowHandle

_GLOSSARY = "glossary.todo.md"
_WARN = "⚠️"

# Human-readable reasons for the "did nothing" outcomes (#100 observability). A
# no-op used to return a bare status token the UI showed as raw JSON; these are
# surfaced as the run's message so the user sees WHY nothing happened, and how to
# fix it. zh-TW per the app's UI language; refer to collections.json by the name
# the user edits it under (README §5).
_MSG_NO_COLLECTIONS = (
    "這個 Hub 還沒有設定任何知識庫，沒有可歸檔的目標。"
    "請先在知識庫清單（collections.json）加入至少一個知識庫，再重新執行。"
)
_MSG_MALFORMED_COLLECTIONS = (
    "知識庫清單（collections.json）有內容，但格式不正確、讀不到任何知識庫。"
    '每一項應為物件，例如 [{"id": "…", "name": "…"}]。請修正後再重新執行。'
)
_MSG_NO_FILES = "沒有找到要歸檔的檔案，已跳過。請把要歸檔的檔案放進 inputs/ 後再執行。"


async def _no_collections_result(wf: WorkflowHandle) -> dict[str, Any]:
    """Why did the collection set come back empty? Distinguish "no list yet"
    (empty / missing) from "list present but unparseable" (malformed) so the user
    gets a fixable reason instead of a silent no-op (#100)."""
    raw: Any = None
    malformed = False
    if await wf.exists("collections.json"):
        try:
            raw = await wf.read_json("collections.json")
        except ValueError:
            malformed = True  # not even valid JSON
    # A non-empty list that _read_collections still parsed to zero means every
    # entry was the wrong shape (e.g. bare strings) → malformed, not empty.
    if malformed or (isinstance(raw, list) and len(raw) > 0):
        return {"status": "malformed_collections", "message": _MSG_MALFORMED_COLLECTIONS}
    return {"status": "no_collections", "message": _MSG_NO_COLLECTIONS}


def _safe(f: str) -> str:
    return f.lstrip("/").replace("/", "_")


def _plan_path(f: str, round: int) -> str:
    return f"plan/r{round}/{_safe(f)}.json"


def _review_phase(round: int) -> str:
    """Round 0 keeps the declared ``review`` phase; each ``revise`` opens a fresh gate
    phase so its decision artifact (``step_<phase>/decision.json``) doesn't read the
    previous round's recorded ``revise``."""
    return "review" if round == 0 else f"review_{round}"


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


def _plan_terms(plan: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """``(term, definition, confident)`` triples from a classify plan, tolerant of a
    bare-string ``terms`` entry (→ uncertain, no draft) for robustness."""
    out: list[tuple[str, str, bool]] = []
    for t in plan.get("terms") or []:
        if isinstance(t, dict):
            term = str(t.get("term", "")).strip()
            if term:
                out.append((term, str(t.get("definition", "")).strip(), bool(t.get("confident"))))
        elif isinstance(t, str) and t.strip():
            out.append((t.strip(), "", False))
    return out


def _classify_check(path: str, allowed: list[str]):
    """The recorded plan picks a collection in the allowed set and carries a usable
    ``terms`` list (manual §8: clamp the agent's choice; a bad shape is fed back so the
    agent re-drafts rather than the run committing garbage)."""

    async def _check(wf: WorkflowHandle, _result: Any) -> CheckResult:
        try:
            obj = await wf.read_json(path)
        except FileNotFound:
            return CheckResult(False, f"expected file {path} was not written")
        if not isinstance(obj, dict):
            return CheckResult(False, f"{path} must be a JSON object")
        if obj.get("collection") not in allowed:
            return CheckResult(
                False, f"collection={obj.get('collection')!r} is not one of {allowed}"
            )
        if not isinstance(obj.get("terms"), list):
            return CheckResult(False, "terms must be a list of {term, definition, confident}")
        return CheckResult(True)

    return _check


def _classify_prompt(f: str, out: str, collections: list[str], feedback: str) -> str:
    base = (
        f"Read the file {f}. Choose the single best collection for it from {collections}. "
        f"Write a one-line digest. Identify the domain terms or abbreviations a newcomer "
        f"would not know. For EACH such term, draft a short plain-language definition based "
        f"on the file, and set confident=true only if you are sure the draft is correct "
        f"(false if you are guessing). Then write a JSON object "
        f'{{"collection": <one of {collections}>, "digest": <text>, "terms": '
        f'[{{"term": <term>, "definition": <your draft>, "confident": <true|false>}}, ...]}} '
        f"to {out} with write_file (use edit_file if {out} already exists). Output nothing else."
    )
    if feedback:
        base += f"\n\nA reviewer asked for changes — apply this when re-drafting: {feedback}"
    return base


def _assemble_glossary(plan_by_file: dict[str, dict[str, Any]]) -> str:
    """Deterministic: one ``## <term>`` section per unique term (first appearance wins).
    A confident draft becomes the definition; an uncertain one becomes a ``⚠️`` line the
    human resolves in the IDE."""
    seen: dict[str, tuple[str, bool]] = {}
    order: list[str] = []
    for plan in plan_by_file.values():
        for term, definition, confident in _plan_terms(plan):
            if term not in seen:
                seen[term] = (definition, confident)
                order.append(term)
    blocks: list[str] = []
    for term in order:
        definition, confident = seen[term]
        if confident and definition:
            body = definition
        elif definition:
            body = f"{_WARN} {definition}"
        else:
            body = f"{_WARN} draft a definition for this term"
        blocks.append(f"## {term}\n{body}\n")
    return "\n".join(blocks)


def _term_collection(plan_by_file: dict[str, dict[str, Any]]) -> dict[str, str]:
    """``term → collection`` (first file wins) so each card lands in the right place."""
    out: dict[str, str] = {}
    for plan in plan_by_file.values():
        coll = plan.get("collection")
        for term, _definition, _confident in _plan_terms(plan):
            if isinstance(coll, str):
                out.setdefault(term, coll)
    return out


def _parse_glossary(text: str) -> list[tuple[str, str]]:
    """Parse the (drafted, possibly human-edited) ``glossary.todo.md`` into
    ``(term, definition)`` pairs. A section counts as *filled* only once its ``⚠️`` lines
    are dropped and something remains — so an unresolved draft authors no card."""
    entries: list[tuple[str, str]] = []
    term: str | None = None
    body: list[str] = []

    def _flush() -> None:
        if term is not None:
            kept = [ln for ln in body if not ln.strip().startswith(_WARN)]
            if filled := "\n".join(kept).strip():
                entries.append((term, filled))

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            term, body = line[3:].strip(), []
        elif term is not None:
            body.append(line)
    _flush()
    return entries


def _gate_summary(files: list[str], plan_by_file: dict[str, dict[str, Any]]) -> str:
    """What the human reviews at the gate (#133): where the drafts live, how many still
    need their input, and the routing — the *content* itself is read/edited in the IDE."""
    drafted: dict[str, bool] = {}  # term → confident (first appearance wins)
    for f in files:
        for term, _definition, confident in _plan_terms(plan_by_file[f]):
            drafted.setdefault(term, confident)
    n_warn = sum(1 for confident in drafted.values() if not confident)
    routing = "; ".join(f"{f} → {plan_by_file[f].get('collection')}" for f in files)
    return "\n".join(
        [
            f"Open {_GLOSSARY} in the file tree, review/edit the definitions, then Approve.",
            f"{len(drafted)} term(s) drafted, {n_warn} still need your input ({_WARN}).",
            f"Routing: {routing}",
        ]
    )


async def _assemble_step(
    wf: WorkflowHandle, round: int, plan_by_file: dict[str, dict[str, Any]]
) -> None:
    """Write the assembled glossary as a deterministic, journaled node so a replay
    (e.g. after the human edits the file and approves) is a cache hit and does NOT
    clobber their edits."""
    content = _assemble_glossary(plan_by_file)

    async def execute(_feedback: str | None) -> dict[str, str]:
        await wf.write(_GLOSSARY, content)
        return {"path": _GLOSSARY}

    await run_step(
        wf,
        name=f"glossary_r{round}",
        phase="glossary",
        args={"round": round, "content": content},
        execute=execute,
    )


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    collections = await _read_collections(wf)
    if not collections:
        return await _no_collections_result(wf)
    files = await wf.glob(
        inputs.get("files", ["inputs/*"]),
        exclude=inputs.get("except", ["inputs/input.json"]),
    )
    if not files:
        return {"status": "empty", "files": 0, "message": _MSG_NO_FILES}

    # PRODUCE → REVIEW, looping on `revise` so each round regenerates the drafts.
    feedback = ""
    round = 0
    plan_by_file: dict[str, dict[str, Any]] = {}
    while True:
        plan_by_file = {}
        for f in files:
            out = _plan_path(f, round)
            await agent_step(
                wf,
                phase="classify",
                name=f"classify_r{round}_{_safe(f)}",
                prompt=_classify_prompt(f, out, collections, feedback),
                tools=["read_file", "write_file", "edit_file"],
                check=_classify_check(out, collections),
                retries=2,
            )
            plan_by_file[f] = await wf.read_json(out)
        await _assemble_step(wf, round, plan_by_file)

        decision = await human_gate(
            wf,
            phase=_review_phase(round),
            title="Review the drafted glossary, then approve",
            summary=_gate_summary(files, plan_by_file),
            allow=["approve", "reject", "revise"],
        )
        if decision.choice == "reject":
            return {"status": "rejected", "files": len(files)}
        if decision.choice == "approve":
            break
        feedback = decision.input
        round += 1

    # COMMIT: ingest each upload + author a card per filled (non-⚠️) glossary entry.
    term_collection = _term_collection(plan_by_file)

    ingested = 0
    for f in files:
        coll = plan_by_file[f]["collection"]
        await wf.ingest_to_collection(coll, f, phase="commit")
        if (await collection_has(coll, f)(wf, None)).ok:
            ingested += 1

    cards = 0
    for term, body in _parse_glossary(await wf.read_text(_GLOSSARY)):
        await wf.upsert_context_card(
            term_collection.get(term, collections[0]),
            [term],
            title=term,
            body=body,
            phase="commit",
        )
        cards += 1

    return {"status": "approved", "ingested": ingested, "cards": cards}

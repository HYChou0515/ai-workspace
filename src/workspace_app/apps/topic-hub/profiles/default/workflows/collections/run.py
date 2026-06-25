"""``→collections`` workflow (topic-hub §12, #133) — the canonical
produce → review → commit, with the review *content* living in files.

PRODUCE: an agent node classifies each upload into one of the Hub's collections
(``collections.json``, §5), writes a digest, and — while it still has the file open —
**drafts a short definition for each unknown domain term**, judging whether it is
confident the draft is right (``plan/r<n>/<f>.json``, gated by ``classify_plan`` so the
agent never holds a side-effecting tool). A deterministic node then assembles those
drafts into the proposed cards file ``context-card.todo.md`` (one ``## <title>`` block per
term carrying ``collection`` / ``keys`` / body): confident drafts become the body, uncertain
ones a ``⚠️`` line for the human to resolve. Alongside it, the node writes a READ-ONLY
"before" snapshot ``.readonly/context-card.current.md`` — for each proposed card, the
EXISTING card a commit-time upsert would overwrite (#205), so the human can diff the two
and never blind-signs an overwrite.

REVIEW: a ``human_gate`` (approve / reject / **revise**). The human opens "查看變更" to
diff ``context-card.todo.md`` (right, editable) against ``.readonly/context-card.current.md``
(left, read-only) — VSCode-style — and edits the proposed cards in place before
**approving** (commit what's there, incl. their edits). **Revise** + feedback re-runs the
whole produce step to regenerate the drafts (overwriting). **Reject** ends the run.

COMMIT (deterministic, idempotent): ``ingest_to_collection`` files each upload and
``upsert_context_card`` (§8, #111) authors a context card for each *filled* block — the
``collection`` is read straight from the block (so a human title edit can't misroute it),
keys/title/body full-overwrite by key. Blocks that are still only a ``⚠️`` line are skipped
(unresolved). Re-run replays completed steps (§9); nothing reaches a collection before
approval.

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

# The proposed cards (editable, committed) + the read-only "before" snapshot the human
# diffs it against (#205). ``.readonly/`` is server-enforced read-only (api/app.py).
_TODO = "context-card.todo.md"
_CURRENT = ".readonly/context-card.current.md"
_WARN = "⚠️"

# The Hub's upload staging folder (#234). Uploaded files live under ``uploads/`` in the
# workspace; this prefix is stripped before a file is ingested so it lands in the
# collection at its bare path (``a.txt``, not ``uploads/a.txt``). Hardcoded for now —
# making the folder profile-configurable is #198.
_UPLOADS = "uploads/"

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
_MSG_NO_FILES = "沒有找到要歸檔的檔案，已跳過。請把要歸檔的檔案放進 uploads/ 後再執行。"


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


def _proposed_cards(plan_by_file: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic: one proposed card per unique term (first appearance wins). Each is
    ``{title, collection, keys, body}`` — title/keys are the term (drafting stays per-key,
    #205), collection is the file's routing. A confident draft becomes the body; an
    uncertain one a ``⚠️`` line the human resolves in the diff."""
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for plan in plan_by_file.values():
        coll = plan.get("collection")
        coll = coll if isinstance(coll, str) else ""
        for term, definition, confident in _plan_terms(plan):
            if term not in seen:
                if confident and definition:
                    body = definition
                elif definition:
                    body = f"{_WARN} {definition}"
                else:
                    body = f"{_WARN} draft a definition for this term"
                seen[term] = {"title": term, "collection": coll, "keys": [term], "body": body}
                order.append(term)
    return [seen[t] for t in order]


def _render_cards(cards: list[dict[str, Any]]) -> str:
    """Render cards as ``## <title>`` blocks carrying ``collection`` / ``keys`` / body
    (#205) — the SAME format for both files, so a whole-file diff lines up block-by-block
    and shows keys/title/body changes (incl. a silent key-narrowing). Empty list → ``""``
    (an empty snapshot = every proposed card is brand-new)."""

    def _block(c: dict[str, Any]) -> str:
        keys = ", ".join(c["keys"])
        return f"## {c['title']}\ncollection: {c['collection']}\nkeys: {keys}\n\n{c['body']}\n"

    return "\n".join(_block(c) for c in cards)


def _parse_cards(text: str) -> list[dict[str, Any]]:
    """Parse the (proposed, possibly human-edited) ``context-card.todo.md`` into
    ``{collection, keys, title, body}`` dicts (#205). ``collection`` / ``keys`` are read
    from their metadata lines (first wins) so a human title edit can't misroute the card;
    everything else is body. A block counts as *filled* only once its ``⚠️`` lines are
    dropped and something remains — an unresolved draft authors no card."""
    cards: list[dict[str, Any]] = []
    title: str | None = None
    collection = ""
    keys: list[str] = []
    body: list[str] = []

    def _flush() -> None:
        if title is not None:
            kept = [ln for ln in body if not ln.strip().startswith(_WARN)]
            if filled := "\n".join(kept).strip():
                cards.append(
                    {
                        "collection": collection,
                        "keys": keys or [title],
                        "title": title,
                        "body": filled,
                    }
                )

    for line in text.splitlines():
        if line.startswith("## "):
            _flush()
            title, collection, keys, body = line[3:].strip(), "", [], []
        elif title is not None:
            s = line.strip()
            if s.startswith("collection:") and not collection:
                collection = s[len("collection:") :].strip()
            elif s.startswith("keys:") and not keys:
                keys = [k.strip() for k in s[len("keys:") :].split(",") if k.strip()]
            else:
                body.append(line)
    _flush()
    return cards


def _gate_summary(
    files: list[str], plan_by_file: dict[str, dict[str, Any]], ambiguous: int = 0
) -> str:
    """What the human reviews at the gate (#133, #205): open "查看變更" to diff the proposed
    cards against the current ones, how many still need their input, any ambiguous overwrite,
    and the routing — the card *content* is reviewed/edited in the diff itself."""
    drafted: dict[str, bool] = {}  # term → confident (first appearance wins)
    for f in files:
        for term, _definition, confident in _plan_terms(plan_by_file[f]):
            drafted.setdefault(term, confident)
    n_warn = sum(1 for confident in drafted.values() if not confident)
    routing = "; ".join(f"{f} → {plan_by_file[f].get('collection')}" for f in files)
    lines = [
        "Open 查看變更 to compare each proposed card against the current one before it's "
        "overwritten, edit if needed, then Approve.",
        f"{len(drafted)} card(s) proposed, {n_warn} still need your input ({_WARN}).",
    ]
    if ambiguous:
        lines.append(
            f"{ambiguous} term(s) match more than one existing card — only the first is "
            "overwritten."
        )
    lines.append(f"Routing: {routing}")
    return "\n".join(lines)


async def _assemble_step(
    wf: WorkflowHandle, round: int, plan_by_file: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Write the proposed cards + the read-only "before" snapshot as one deterministic,
    journaled node (#205) so a replay (after the human edits the file and approves) is a
    cache hit and does NOT clobber their edits — nor re-snapshot away from what they
    reviewed. For each proposed card, look up the EXISTING card a commit-time upsert would
    overwrite and render it into ``.readonly/context-card.current.md`` (empty block-set when
    none exists → diff shows pure additions). Returns the step result, incl. how many terms
    were ambiguous (matched >1 card) for the gate summary."""
    proposed = _proposed_cards(plan_by_file)
    todo_content = _render_cards(proposed)

    async def execute(_feedback: str | None) -> dict[str, Any]:
        current: list[dict[str, Any]] = []
        ambiguous = 0
        for card in proposed:
            existing = await wf.find_overwrite_card(
                card["collection"], card["keys"], title=card["title"]
            )
            if existing is not None:
                current.append(
                    {
                        "title": existing["title"],
                        "collection": card["collection"],  # same collection (scoped lookup)
                        "keys": existing["keys"],
                        "body": existing["body"],
                    }
                )
                if existing.get("ambiguity", 0) > 1:
                    ambiguous += 1
        await wf.write(_TODO, todo_content)
        await wf.write(_CURRENT, _render_cards(current))
        return {"todo": _TODO, "current": _CURRENT, "ambiguous": ambiguous}

    return await run_step(
        wf,
        name=f"cards_r{round}",
        phase="glossary",
        args={"round": round, "content": todo_content},
        execute=execute,
    )


async def run(wf: WorkflowHandle, inputs: dict[str, Any]) -> dict[str, Any]:
    collections = await _read_collections(wf)
    if not collections:
        return await _no_collections_result(wf)
    files = await wf.glob(
        inputs.get("files", [f"{_UPLOADS}*"]),
        exclude=inputs.get("except", [f"{_UPLOADS}input.json"]),
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
        assembled = await _assemble_step(wf, round, plan_by_file)

        decision = await human_gate(
            wf,
            phase=_review_phase(round),
            title="Review the proposed context cards, then approve",
            summary=_gate_summary(files, plan_by_file, assembled.get("ambiguous", 0)),
            allow=["approve", "reject", "revise"],
        )
        if decision.choice == "reject":
            return {"status": "rejected", "files": len(files)}
        if decision.choice == "approve":
            break
        feedback = decision.input
        round += 1

    # COMMIT: ingest each upload + author a card per filled (non-⚠️) block. The collection
    # is read from the block itself (#205) so a human title edit can't misroute it; a block
    # with no/blank collection falls back to the first Hub collection, and a collection that
    # isn't one of the Hub's is rejected loudly rather than silently mis-filing the card.
    ingested = 0
    for f in files:
        coll = plan_by_file[f]["collection"]
        # #234: the ``uploads/`` staging prefix is NOT part of the doc's path in the
        # collection — strip it so the doc lands at its bare path (``a.txt``, not
        # ``uploads/a.txt``). The same stripped path feeds the landing check so both
        # resolve to the same natural-key id.
        dest = f.removeprefix("/").removeprefix(_UPLOADS)
        await wf.ingest_to_collection(coll, dest, phase="commit")
        if (await collection_has(coll, dest)(wf, None)).ok:
            ingested += 1

    cards = 0
    for card in _parse_cards(await wf.read_text(_TODO)):
        coll = card["collection"] or collections[0]
        if coll not in collections:
            raise ValueError(
                f"card {card['title']!r} names collection {coll!r}, which is not one of the "
                f"Hub's collections {collections} — fix it in the diff and re-approve"
            )
        await wf.upsert_context_card(
            coll, card["keys"], title=card["title"], body=card["body"], phase="commit"
        )
        cards += 1

    return {"status": "approved", "ingested": ingested, "cards": cards}

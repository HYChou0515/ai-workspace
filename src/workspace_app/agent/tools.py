from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
from typing import TYPE_CHECKING

import magic
from agents import FunctionTool, RunContextWrapper, function_tool

from ..files import WorkspaceFiles
from ..filestore.protocol import FileNotFound
from ..sandbox.protocol import ExecResult
from .context import AgentToolContext

if TYPE_CHECKING:
    from ..resources.conversation import Citation

_LOGGER = logging.getLogger(__name__)


def _truncate_middle(text: str, max_chars: int) -> str:
    """Cap `text` at `max_chars` keeping the HEAD and the TAIL (issue #44).

    A `grep`/log dump's useful bits cluster at both ends — the first
    matches up top, the count / error / summary at the bottom — so a
    head-only cut throws away the punchline. We keep ~2/3 of the budget
    for the head, ~1/3 for the tail, trim each to a line boundary, and
    drop a marker in between that tells the agent to narrow its command.
    """
    if len(text) <= max_chars:
        return text
    head_budget = max_chars * 2 // 3
    tail_budget = max_chars - head_budget
    head = text[:head_budget]
    nl = head.rfind("\n")
    if nl > 0:  # cut on a line boundary so we don't split a line mid-token
        head = head[:nl]
    tail = text[len(text) - tail_budget :]
    nl = tail.find("\n")
    if nl != -1:
        tail = tail[nl + 1 :]
    omitted = len(text) - len(head) - len(tail)
    marker = (
        f"\n\n… [{omitted} chars omitted — narrow the command "
        f"(e.g. grep/head/tail/wc) to see the part you need] …\n\n"
    )
    return head + marker + tail


def _format_exec(
    name: str, r: ExecResult, max_chars: int | None = None, *, keep_stderr: bool = False
) -> str:
    """Format an ExecResult. See tests/agent/test_format_exec.py for the
    contract — name prefix anchors attribution; stderr is suppressed on
    success; the body is capped head+tail at `max_chars` (issue #44 —
    `None` disables the cap, e.g. in unit tests).

    `keep_stderr` (#62) is the FE/display surface, decoupled from the
    LLM-facing result: it keeps a *successful* command's stderr so the
    error the user saw stream live doesn't vanish from the final tool
    card. The default (LLM) still drops success-stderr as noise."""
    stdout = r.stdout.decode("utf-8", errors="replace")
    header = f"Tool `{name}` returned (exit_code={r.exit_code}):"
    # On failure stderr is where the error lives — always show it. On
    # success it's by convention noise (progress logs, deprecation
    # warnings) that misleads small models, so the LLM-facing form drops
    # it; the display form (`keep_stderr`) keeps it when present.
    if r.exit_code != 0 or (keep_stderr and r.stderr):
        stderr = r.stderr.decode("utf-8", errors="replace")
        body = f"{stdout}\n--- stderr ---\n{stderr}"
    else:
        body = stdout
    if max_chars is not None:
        # Cap the BODY only — the header (exit code) is tiny and must
        # always survive so the model can read the outcome.
        body = _truncate_middle(body, max_chars)
    return f"{header}\n{body}"


def _workspace(ctx: RunContextWrapper[AgentToolContext]) -> tuple[WorkspaceFiles, str]:
    """The (file facade, investigation_id) the RCA file tools require. When the
    caller didn't inject a facade, wrap the bare filestore (transitional)."""
    inv = ctx.context.investigation_id
    files = ctx.context.files
    if files is None:
        assert ctx.context.filestore is not None  # file tools imply an RCA context
        files = WorkspaceFiles(ctx.context.filestore)
    assert inv is not None
    return files, inv


async def exec_impl(ctx: RunContextWrapper[AgentToolContext], cmd: list[str]) -> str:
    """Run a shell command inside the workspace sandbox. This is the only thing
    that wakes a cold sandbox: ensure_sandbox creates it and restores the
    snapshot into it, so any file writes the agent made while cold are present;
    from here on the sandbox IS the source of truth and the file tools route to
    it directly (no flush needed)."""
    assert ctx.context.sandbox is not None
    handle = await ctx.context.ensure_sandbox()
    # Stream stdout live (when the runner wired a sink) so a long-running
    # command's output shows up in run history as it happens.
    result = await ctx.context.sandbox.exec(handle, cmd, on_output=ctx.context.on_exec_output)
    return _exec_result_text(ctx.context, "exec", result)


def _exec_result_text(ctx: AgentToolContext, name: str, result: ExecResult) -> str:
    """The cleaned, LLM-facing exec result — and, when it would differ,
    record the FULL display result (success-stderr kept) on the context so
    the runner can attach it to the ToolEnd (#62). Returns the cleaned form
    (what the model and `history_items` consume)."""
    cap = ctx.exec_output_max_chars
    cleaned = _format_exec(name, result, max_chars=cap)
    display = _format_exec(name, result, max_chars=cap, keep_stderr=True)
    if display != cleaned:
        ctx.tool_displays[cleaned] = display
    return cleaned


async def read_file_impl(
    ctx: RunContextWrapper[AgentToolContext],
    path: str,
    offset: int | None = None,
    limit: int | None = None,
) -> str:
    """Read a file from the workspace. Returns a window of lines: `offset` is the
    1-based first line (default 1), `limit` the number of lines (default: the
    configured cap). A large file is truncated — by line count and by a total
    character budget — with a notice; page through it with `offset`/`limit`."""
    fs, inv = _workspace(ctx)
    try:
        data = await fs.read(inv, path)
    except FileNotFound:
        return f"error: file not found: {path}"

    lines = data.decode("utf-8", errors="replace").split("\n")
    total = len(lines)
    start = max(0, (offset or 1) - 1)
    count = limit if limit is not None else ctx.context.read_file_max_lines
    window = lines[start : start + count]
    body = "\n".join(window)

    notices: list[str] = []
    end = start + len(window)
    if start > 0 or end < total:
        notices.append(f"showing lines {start + 1}-{end} of {total}")
    max_chars = ctx.context.read_file_max_chars
    if len(body) > max_chars:
        body = body[:max_chars]
        notices.append(f"output capped at {max_chars} chars")
    if notices:
        body += f"\n\n[truncated: {'; '.join(notices)} — use offset/limit to read more]"
    return body


async def read_image_impl(
    ctx: RunContextWrapper[AgentToolContext],
    path: str,
    question: str | None = None,
) -> str:
    """Look at an image file in the workspace and get a text answer about it.

    Use this for screenshots, charts, photos, or diagrams — `read_file`
    returns raw bytes for these and is useless. `path` is the workspace path
    to the image. `question` optionally focuses the read (e.g. "what error is
    in this screenshot?"); omit it for a full description of everything
    visible.
    """
    describer = ctx.context.describer
    if describer is None:
        return (
            "error: image reading is not available — this deployment has no "
            "vision model configured. Do not retry."
        )
    fs, inv = _workspace(ctx)
    try:
        data = await fs.read(inv, path)
    except FileNotFound:
        return f"error: file not found: {path}"

    mime = magic.from_buffer(data, mime=True)
    if not mime.startswith("image/"):
        return f"error: not an image file: {path} (detected {mime})"

    sink = ctx.context.on_exec_output
    on_chunk = (lambda t, _r: sink(t.encode("utf-8"))) if sink is not None else None
    if question:
        out = describer.answer(data, mime, question=question, on_chunk=on_chunk)
    else:
        out = describer.describe(data, mime, on_chunk=on_chunk)
    return _truncate_middle(out, ctx.context.read_file_max_chars)


async def write_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str, content: str) -> str:
    """Create a NEW file. This never overwrites: if the file already exists it
    is rejected and the current content is returned — use `edit_file` to change
    an existing file (so you always state what you expect to replace). This is
    what stops blind writes."""
    fs, inv = _workspace(ctx)
    current = await fs.create(inv, path, content.encode("utf-8"))
    if current is None:
        return f"wrote {len(content)} bytes to {path}"
    return (
        f"error: {path} already exists — use edit_file to modify it (or delete "
        f"it first). Current content:\n{current.decode('utf-8', errors='replace')}"
    )


async def edit_file_impl(
    ctx: RunContextWrapper[AgentToolContext], path: str, old_string: str, new_string: str
) -> str:
    """Edit an existing file by replacing `old_string` with `new_string`.
    `old_string` must match the current file content **exactly and uniquely**
    (include enough surrounding context). If it isn't found or matches more than
    once — including because someone else changed the file since you read it —
    the edit is rejected and the current content is returned, so re-read it and
    try again. To rewrite a whole file, pass its entire current content as
    `old_string`."""
    fs, inv = _workspace(ctx)
    current = await fs.edit(inv, path, old_string, new_string)
    if current is None:
        return f"edited {path}"
    return (
        f"error: could not apply the edit to {path} — `old_string` was not found "
        f"exactly once (the file may have changed). Current content:\n{current}"
    )


async def ls_impl(ctx: RunContextWrapper[AgentToolContext], prefix: str = "") -> list[str]:
    """List files in the workspace file store, optionally filtered by prefix."""
    fs, inv = _workspace(ctx)
    return await fs.ls(inv, prefix)


async def exists_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> bool:
    """Check whether a file exists in the workspace file store."""
    fs, inv = _workspace(ctx)
    return await fs.exists(inv, path)


async def delete_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Delete a file from the workspace file store."""
    fs, inv = _workspace(ctx)
    try:
        await fs.delete(inv, path)
    except FileNotFound:
        return f"error: file not found: {path}"
    return f"deleted {path}"


# ── wiki agent tools (#50) ───────────────────────────────────────────


async def search_wiki_impl(ctx: RunContextWrapper[AgentToolContext], query: str) -> str:
    """Search the wiki pages for `query` (case-insensitive substring) and
    return matching lines as ``path:line: text`` — Karpathy's grep over the
    wiki, sandbox-free (in-process over the FileStore). Use it to find which
    existing pages mention a term before updating them, or to locate the
    pages relevant to a question."""
    from ..api.search import InvalidQuery, compile_query, search_text

    fs, inv = _workspace(ctx)
    try:
        pattern = compile_query(query)
    except InvalidQuery as exc:
        return f"error: invalid search {query!r}: {exc}"
    hits: list[str] = []
    for path in sorted(await fs.ls(inv)):
        try:
            data = await fs.read(inv, path)
        except FileNotFound:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for m in search_text(text, pattern):
            hits.append(f"{path}:{m.line}: {m.text}")
    if not hits:
        return f"no wiki pages match {query!r}"
    body = "\n".join(hits)
    cap = ctx.context.exec_output_max_chars
    return _truncate_middle(body, cap) if len(body) > cap else body


async def read_new_source_impl(ctx: RunContextWrapper[AgentToolContext]) -> str:
    """Read the source document that triggered this wiki-maintenance run —
    the new/changed material to fold into the wiki."""
    src = ctx.context.wiki_new_source
    if not src:
        return "error: no new source for this run"
    cap = ctx.context.exec_output_max_chars
    return _truncate_middle(src, cap) if len(src) > cap else src


async def list_sources_impl(ctx: RunContextWrapper[AgentToolContext]) -> list[str]:
    """List the collection's raw source documents (read-only) so you can
    re-read or cross-reference any of them while maintaining the wiki."""
    sources = ctx.context.wiki_sources
    return sources.list() if sources is not None else []


_WIKI_SNIPPET_MAX = 1200  # citation snippet cap (the FE reference card excerpt)


async def read_source_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Read one raw source document's text by its path (read-only). Use it to
    verify a fact before writing it into a wiki page, to record a page's
    ``Sources:`` provenance, and (as the reader) to ground an answer in the
    real document — cite the returned [n].

    On a reader run the result is a numbered ``[n] filename: text`` reference
    (so you cite claims with the matching [n], like kb_search); on a maintainer
    run it's the plain text."""
    from ..resources.kb import RetrievedPassage

    sources = ctx.context.wiki_sources
    if sources is None:
        return f"error: source not found: {path}"

    if not ctx.context.wiki_cite_sources:
        # Maintainer path: plain text for cross-referencing.
        text = sources.read(path)
        if text is None:
            return f"error: source not found: {path}"
        cap = ctx.context.exec_output_max_chars
        return _truncate_middle(text, cap) if len(text) > cap else text

    # Reader path: register the source as a citable passage (dedup by doc id,
    # whole-document granularity) and hand it back numbered so [n] resolves to
    # the underlying SourceDoc via parse_citations.
    ref = sources.ref(path)
    if ref is None:
        return f"error: source not found: {path}"
    registry = ctx.context.kb_passages
    seen = {p.document_id: i for i, p in enumerate(registry)}
    idx = seen.get(ref.document_id)
    if idx is None:
        idx = len(registry)
        registry.append(
            RetrievedPassage(
                collection_id=ref.collection_id,
                document_id=ref.document_id,
                filename=ref.path.rsplit("/", 1)[-1],
                start=0,
                end=len(ref.text),
                source_chunk_ids=[],
                text=ref.text[:_WIKI_SNIPPET_MAX],
                score=0.0,
            )
        )
    cap = ctx.context.exec_output_max_chars
    body = ref.text if len(ref.text) <= cap else _truncate_middle(ref.text, cap)
    return f"[{idx + 1}] {ref.path.rsplit('/', 1)[-1]}: {body}"


def kb_search_impl(
    ctx: RunContextWrapper[AgentToolContext],
    query: str,
    expand: int | None = None,
    hyde: int | None = None,
    rerank: bool | None = None,
) -> str:
    """Semantic search over the knowledge base; returns numbered passages to cite as [n].

    This is VECTOR retrieval — it matches on MEANING, not keywords. Pass a
    natural-language question or a short description of what you need (the way
    you'd ask a person), NOT keywords or a Google-style query. One well-phrased
    query usually returns the relevant passages; READ them before searching
    again. Only search again for GENUINELY DIFFERENT information (a new
    entity/term/sub-topic the results surfaced) — never re-run a reworded version
    of the same question (it's slow and returns the same passages). Each result
    is numbered globally across the turn; cite a claim with the matching [n].
    Numbers persist across calls, so [1] always means the same passage.

    The optional `expand` / `hyde` / `rerank` knobs override the operator's
    retrieval enhancement defaults for THIS call only — set them when the
    query needs more recall (raise `expand` / `hyde`) or when a quick lookup
    doesn't need the rerank LLM round-trip. The operator's `max` clamps
    whatever you pass, so requesting `expand=99` is safe.
    """
    from ..kb.retriever import Enhancements

    retriever = ctx.context.retriever
    assert retriever is not None  # kb_search implies a KB context
    registry = ctx.context.kb_passages
    seen = {(p.document_id, p.start, p.end): i for i, p in enumerate(registry)}

    # Resolution cascade: caller (context) > LLM tool args > retriever
    # default (#68). When the KB-chat user picks a depth, the caller sets
    # expand/hyde/rerank explicitly — that's authoritative, so a model that
    # fills in its own deeper args can't quietly override the user's "quick".
    # The model's args only take effect for knobs the caller left unset
    # (e.g. "standard", which sends no depth payload). The retriever does
    # the last step (default + operator-max clamp).
    caller = ctx.context.kb_enhancements
    effective = Enhancements(
        expand=caller.expand if caller and caller.expand is not None else expand,
        hyde=caller.hyde if caller and caller.hyde is not None else hyde,
        rerank=caller.rerank if caller and caller.rerank is not None else rerank,
    )

    # Stream the retriever's enhancement-LLM work (multi-query / HyDE / rerank)
    # as this tool's live output, so its thinking shows in the chat (issue #10).
    sink = ctx.context.on_exec_output
    on_progress = (lambda text, _reasoning: sink(text.encode())) if sink is not None else None

    lines: list[str] = []
    try:
        for passage in retriever.search(
            query,
            ctx.context.collection_ids,
            on_progress,
            enhancements=effective,
        ):
            key = (passage.document_id, passage.start, passage.end)
            idx = seen.get(key)
            if idx is None:
                idx = len(registry)
                seen[key] = idx
                registry.append(passage)
            lines.append(f"[{idx + 1}] {passage.filename}: {passage.text}")
    except Exception:
        # Log the real cause (with traceback) to the server log so the
        # operator sees what actually broke — connection refused,
        # LiteLLM HTTP error, retrieval LLM down, etc. Without this the
        # exception goes straight into the agents-SDK's tool-error
        # wrapper as a one-line string and the server log stays silent.
        # We re-raise so the SDK still surfaces the error to the agent
        # (which `answer_question` then captures and surfaces upstream).
        _LOGGER.exception("kb_search failed for query=%r", query)
        raise

    if not lines:
        return "No matching passages in the knowledge base."
    return "\n\n".join(lines)


async def ask_knowledge_base_impl(ctx: RunContextWrapper[AgentToolContext], question: str) -> str:
    """Ask the knowledge-base agent a question about the in-house documents.

    Use this ONLY when answering needs facts, procedures, or history that live
    in the in-house knowledge base rather than in the workspace files. Returns a
    synthesized answer with a Sources list. Phrase a focused question, not just
    keywords.

    Do NOT use for: greetings, small-talk, the agent's own name or identity,
    meta-questions about this assistant, or general knowledge you already know.
    For any of those, answer directly without calling this tool.
    """
    run = ctx.context.run_subagent
    assert run is not None  # the API layer wires this for RCA runs
    answer, citations = await run(
        "kb_chat",
        question,
        ctx.context.on_exec_output,
        ctx.context.investigation_id,
    )
    # Citations are bucketed by TOOL NAME (the surface that produced
    # them), not by sub-agent purpose. persist() pairs the Nth bucket
    # entry with the Nth tool message of that name.
    ctx.context.subagent_citations.setdefault("ask_knowledge_base", []).append(citations)
    return answer


def _read_step_names(text: str, column: str) -> list[str]:
    """Unique step names (input order) from `text`. If it parses as CSV
    whose header contains `column`, read that column; otherwise treat each
    non-empty line as one step name. ~1500 steps is normal, so the input
    is a file, not an inline list (#66)."""
    text = text.strip()
    if not text:
        return []
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0] if rows else []
    seen: dict[str, None] = {}
    if column in header:
        idx = header.index(column)
        for r in rows[1:]:
            if idx < len(r) and (v := r[idx].strip()):
                seen.setdefault(v, None)
    else:
        for line in text.splitlines():
            if v := line.strip():
                seen.setdefault(v, None)
    return list(seen)


def _parse_module_json(answer: str) -> tuple[str, str]:
    """Extract `(module, reason)` from a per-step classifier reply. The
    sub-agent is asked for a bare `{"module": ..., "reason": ...}` object;
    we tolerate prose / code fences / a trailing Sources footer by pulling
    the outermost `{...}`. Anything unparseable or an empty module →
    `("unknown", reason)` so the caller writes `unknown` rather than
    aborting the whole run."""
    match = re.search(r"\{.*\}", answer, re.DOTALL)
    if not match:
        return "unknown", ""
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return "unknown", ""
    if not isinstance(obj, dict):  # pragma: no cover — `re.search(r"\{.*\}")` only
        # matches a substring starting with `{`; valid JSON so anchored is always
        # an object, so json.loads here can never yield a non-dict (it raises first).
        return "unknown", ""
    module = obj.get("module")
    reason = obj.get("reason")
    reason = reason if isinstance(reason, str) else ""
    if not isinstance(module, str) or not module.strip():
        return "unknown", reason
    return module.strip(), reason


def _module_map_csv(rows: list[tuple[str, str, str]]) -> bytes:
    """Render `(step_name, module, reason)` rows to a pandera-validated
    module-map CSV (#66). The schema is the contract downstream `qtime-data`
    + the agent rejoin on — step_name/module never null."""
    import pandas as pd
    import pandera.pandas as pa

    df = pd.DataFrame(
        {
            "step_name": [r[0] for r in rows],
            "module": [r[1] for r in rows],
            "reason": [r[2] for r in rows],
        }
    )
    schema = pa.DataFrameSchema(
        {
            "step_name": pa.Column(str, nullable=False),
            "module": pa.Column(str, nullable=False),
            "reason": pa.Column(str, nullable=True, coerce=True),
        }
    )
    schema.validate(df)
    return df.to_csv(index=False).encode("utf-8")


def _infer_modules_summary(rows: list[tuple[str, str, str]], out: str) -> str:
    """Compact, LLM-facing summary as a JSON object — counts only, never the
    per-step names (which would bloat the turn at ~1500 steps). The full map
    lives in the written CSV (`out`). Fields:

      counts_topk   the top-5 real modules by count (high→low), {name: count}
      total_counts  total steps classified
      total_kind    number of distinct real modules (excl. Other / Unknown)
      Others        count of steps the model placed in `Other`
      Unknown       count of steps the tool couldn't classify (`unknown`)
      out           where the per-step module-map CSV was written
    """
    counts: dict[str, int] = {}
    for _step, module, _reason in rows:
        counts[module] = counts.get(module, 0) + 1
    others = counts.pop("Other", 0)
    unknown = counts.pop("unknown", 0)
    topk = dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
    return json.dumps(
        {
            "counts_topk": topk,
            "total_counts": len(rows),
            "total_kind": len(counts),
            "Others": others,
            "Unknown": unknown,
            "out": out,
        },
        ensure_ascii=False,
    )


async def infer_modules_impl(
    ctx: RunContextWrapper[AgentToolContext],
    path: str,
    column: str = "step_name",
    out: str = "step2-data/module-map.csv",
    defect_context: str | None = None,
) -> str:
    """Classify EVERY process step in a file into its fab-process module
    (`STI` / `Gate` / `Contact` / `M1`–`M6` / `Pad` / `Pass` / `Other`, or
    a KB-justified fab-specific name) and write the result to a CSV.

    Use this after pulling wafer-history but BEFORE Q-Time analysis — the
    module mapping is the structural backbone for both.

    `path` is a workspace file (typically `wafer-history.csv`); the unique
    values of its `column` (default `step_name`) are each classified by a
    focused KB-backed sub-agent, ONE step at a time (run in parallel), so
    nothing is skipped even at ~1500 steps. The tool writes `out` (default
    `step2-data/module-map.csv`) with columns `step_name,module,reason` and
    returns a short summary (per-module counts + any steps it couldn't
    classify, which are written as `unknown`). A non-CSV file is read as a
    plain one-step-per-line list.

    Pass `defect_context` (e.g. the brief.md defect type) to bias the
    classifier towards modules physically relevant to the defect when a
    step is ambiguous.
    """
    fs, inv = _workspace(ctx)
    try:
        data = await fs.read(inv, path)
    except FileNotFound:
        return f"error: file not found: {path}"
    steps = _read_step_names(data.decode("utf-8", errors="replace"), column)
    if not steps:
        return f"error: no step names found in {path} (looked for column {column!r})"

    run = ctx.context.run_subagent
    assert run is not None  # the API layer wires this for RCA runs
    sink = ctx.context.on_exec_output
    origin = ctx.context.investigation_id
    sem = asyncio.Semaphore(max(1, ctx.context.infer_modules_parallelism))

    async def classify(step: str) -> tuple[str, str, str, list[Citation]]:
        payload = json.dumps(
            {"step_name": step, "defect_context": defect_context}, ensure_ascii=False
        )
        try:
            async with sem:
                answer, cites = await run("infer_modules", payload, sink, origin)
        except Exception as exc:  # noqa: BLE001 — one step failing must not sink the batch
            return step, "unknown", f"classification error: {type(exc).__name__}: {exc}", []
        module, reason = _parse_module_json(answer)
        return step, module, reason, cites

    results = await asyncio.gather(*(classify(s) for s in steps))

    rows: list[tuple[str, str, str]] = []
    all_cites: list[Citation] = []
    for step, module, reason, cites in results:
        rows.append((step, module, reason))
        all_cites.extend(cites)

    csv_bytes = _module_map_csv(rows)
    # Overwrite: re-running a build replaces the map. create() refuses an
    # existing path (returns its content), so delete first when present.
    if await fs.create(inv, out, csv_bytes) is not None:
        await fs.delete(inv, out)
        await fs.create(inv, out, csv_bytes)

    ctx.context.subagent_citations.setdefault("infer_modules", []).append(all_cites)
    return _infer_modules_summary(rows, out)


async def mention_user_impl(
    ctx: RunContextWrapper[AgentToolContext], user_id: str, reason: str = ""
) -> str:
    """Summon a human teammate to look at this investigation.

    Use when the case needs a person — a domain expert, the owner, a reviewer.
    They get a notification linking here. Pass their user id and a short reason.
    """
    mention = ctx.context.mention
    assert mention is not None  # the API layer wires this for RCA runs
    investigation_id = ctx.context.investigation_id
    assert investigation_id is not None  # mentions belong to an investigation
    mention(investigation_id, [user_id], reason)
    return f"Notified {user_id} to come look at this investigation."


async def read_skill_impl(ctx: RunContextWrapper[AgentToolContext], name: str) -> str:
    """Load a skill's body markdown by name. Progressive disclosure: the
    system prompt's "Available skills" index already lists `(name,
    description)`; this tool returns the *body* on demand so large
    methodologies don't bloat every turn.

    Returns a friendly error string (not raise) for the agent to recover
    from — unknown name lists the available skills, body-cap exceeded
    explains the deployer should split the skill. Host-side only: never
    wakes the sandbox (skills are pure host markdown)."""
    from ..apps.skills import SkillError, list_skills, load_skill

    slug = ctx.context.app_slug
    profile = ctx.context.template_profile
    if slug is None or profile is None:
        return "error: read_skill is only available in an App workspace turn"
    try:
        return load_skill(slug, profile, name)
    except SkillError as e:
        avail = ", ".join(m.name for m in list_skills(slug, profile)) or "(none)"
        return f"error: {e}. available skills: {avail}"


def resolve_collection_impl(ctx: RunContextWrapper[AgentToolContext], ref: str) -> str:
    """Resolve a collection id-or-name to its canonical {id, name} (JSON).

    Use this when the user asks to add or switch a collection: pass the id or name
    they gave, then write the returned {id, name} into `collections.json` yourself
    with write_file / edit_file. Returns a JSON object whose `status` is `ok` (with
    `id` + `name`), `ambiguous` (with `candidates`), or `not_found` (with the
    `available` collections). This only LOOKS UP — it never edits the file."""
    from ..kb.collections import resolve_collection

    spec = ctx.context.spec
    if spec is None:
        return "error: resolve_collection is only available in a Topic Hub turn"
    return json.dumps(resolve_collection(spec, ref), ensure_ascii=False)


def lookup_glossary_impl(ctx: RunContextWrapper[AgentToolContext], query: str) -> str:
    """Look up the Hub's glossary (context cards) for a term or phrase.

    Deterministic + instant — no knowledge-base search. Pass a term you don't
    recognise (or the sentence containing it); returns any matching glossary entries
    as authoritative context (each tagged with its ``card_id`` so you can update it
    via update_context_card), or a short "not found" note. Prefer this BEFORE
    ask_knowledge_base for jargon / abbreviations / domain terms."""
    from ..kb.context_cards import (
        card_context_block,
        cards_with_ids_for_collections,
        match_with_ids,
    )

    spec = ctx.context.spec
    if spec is None:
        return "error: lookup_glossary needs a collection-scoped context (no spec on this turn)"
    pairs = cards_with_ids_for_collections(spec, ctx.context.collection_ids)
    hits = match_with_ids(query, pairs)
    block = card_context_block([c for _, c in hits], ids=[rid for rid, _ in hits])
    return block or f"No glossary entries found for: {query}"


def update_context_card_impl(
    ctx: RunContextWrapper[AgentToolContext],
    card_id: str,
    keys: list[str],
    title: str,
    body: str,
    expected_body: str,
) -> str:
    """Update an EXISTING glossary card (#111), overwriting it with new content.

    Read the card FIRST with lookup_glossary — copy its `card_id` here, and pass the
    body you just read as `expected_body` so a stale edit can't clobber a newer one.
    `keys`/`title`/`body` fully REPLACE the card's current values (merge any content you
    want to keep into `body` yourself). Use this when a term already has a card and you
    mean the SAME thing; for a same-term-different-meaning entry, use create_context_card
    instead. Returns a confirmation, or an `error:` note (re-read and retry on a clash)."""
    from ..workflow.capabilities import CardConflict, CardNotFound, update_context_card

    spec = ctx.context.spec
    if spec is None:
        return "error: update_context_card needs a collection-scoped context (no spec on this turn)"
    try:
        update_context_card(
            spec,
            card_id=card_id,
            keys=keys,
            title=title,
            body=body,
            user=ctx.context.acting_user,
            expected_body=expected_body,
        )
    except CardNotFound:
        return f"error: no glossary card with id {card_id!r} (it may have been deleted) — re-read"
    except CardConflict:
        return (
            f"error: card {card_id!r} changed since you read it — re-read it with "
            "lookup_glossary and retry with the current body as expected_body"
        )
    return f"Updated glossary card {card_id}."


def create_context_card_impl(
    ctx: RunContextWrapper[AgentToolContext],
    collection: str,
    keys: list[str],
    title: str,
    body: str,
) -> str:
    """Create a NEW glossary card in a collection (#111).

    Use this only for a term that has NO card yet, or a same-term-DIFFERENT-meaning
    entry. If an exact key already exists in the collection, this REFUSES and returns
    the existing card id — update that card with update_context_card instead (read it
    first). `collection` is an id or name. Returns a confirmation or an `error:` note."""
    from ..kb.context_cards import find_cards_by_key
    from ..workflow.capabilities import (
        CollectionNotFound,
        create_context_card,
        resolve_collection_id,
    )

    spec = ctx.context.spec
    if spec is None:
        return "error: create_context_card needs a collection-scoped context (no spec on this turn)"
    try:
        collection_id = resolve_collection_id(spec, collection)
    except CollectionNotFound:
        return f"error: unknown collection {collection!r}"
    for key in keys:
        existing = find_cards_by_key(spec, collection_id, key)
        if existing:
            ids = ", ".join(rid for rid, _ in existing)
            return (
                f"error: a card for key {key!r} already exists in this collection "
                f"(card_id: {ids}) — update it with update_context_card instead of "
                "creating a duplicate (read it first with lookup_glossary)"
            )
    card_id = create_context_card(
        spec,
        collection=collection_id,
        keys=keys,
        title=title,
        body=body,
        user=ctx.context.acting_user,
    )
    return f"Created glossary card {card_id}."


_IMPLS = {
    "exec": exec_impl,
    "read_file": read_file_impl,
    "read_image": read_image_impl,
    "write_file": write_file_impl,
    "edit_file": edit_file_impl,
    "ls": ls_impl,
    "exists": exists_impl,
    "delete_file": delete_file_impl,
    "mention_user": mention_user_impl,
    "ask_knowledge_base": ask_knowledge_base_impl,
    "infer_modules": infer_modules_impl,
    "kb_search": kb_search_impl,
    # Topic Hub tools — query specstar resources via ctx.spec (no retriever).
    "resolve_collection": resolve_collection_impl,
    "lookup_glossary": lookup_glossary_impl,
    "update_context_card": update_context_card_impl,
    "create_context_card": create_context_card_impl,
    # Wiki agent tools (#50). Opt-in via the wiki presets' allowed_tools;
    # not in _WORKSPACE_TOOLS (they need a wiki context).
    "search_wiki": search_wiki_impl,
    "read_new_source": read_new_source_impl,
    "list_sources": list_sources_impl,
    "read_source": read_source_impl,
    # `read_skill` is opt-in (#29 / §A): only registered when the active
    # template profile has any skills. `build_tools(profile=)` handles
    # the conditional injection — never present in `_WORKSPACE_TOOLS`.
    "read_skill": read_skill_impl,
}

# The RCA workspace toolset — what `build_tools(None)` hands out. It includes
# ask_knowledge_base (the RCA agent consults the KB through it). `kb_search`
# lives in `_IMPLS` for lookup but is opt-in only — it's the KB agent's OWN
# tool and needs a retriever in the context, which RCA runs never set.
_WORKSPACE_TOOLS = [
    "exec",
    "read_file",
    "write_file",
    "edit_file",
    "ls",
    "exists",
    "delete_file",
    "ask_knowledge_base",
    "infer_modules",
    "mention_user",
]


def build_tools(
    allowed: list[str] | None = None,
    *,
    app_slug: str | None = None,
    profile: str | None = None,
) -> list[FunctionTool]:
    """Build FunctionTool list for the Agent. If `allowed` is None, the
    workspace toolset (file/exec); otherwise exactly the named tools.

    When `app_slug` + `profile` are set and that App profile ships any skills,
    `read_skill` is appended (issue #29 / §A — "skill index + tool same flag
    in/out"). Set per turn from the item's App + profile."""
    names = allowed if allowed is not None else _WORKSPACE_TOOLS
    # Skip names that aren't built-ins — they may be provisioned tool-package
    # commands (#21, #25), which the runner adds separately via
    # `workspace_app.tooling.registry.build_function_tools`. The colon syntax
    # entries (`pkg:cmd`) likewise aren't built-ins and fall through here.
    tools = [function_tool(_IMPLS[n], name_override=n) for n in names if n in _IMPLS]
    if app_slug is not None and profile is not None:
        from ..apps.skills import list_skills

        if list_skills(app_slug, profile):
            tools.append(function_tool(_IMPLS["read_skill"], name_override="read_skill"))
    return tools

from __future__ import annotations

import asyncio
import base64
import csv
import functools
import inspect
import io
import json
import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import magic
from agents import FunctionTool, RunContextWrapper, ToolOutputImage, function_tool

from ..files import WorkspaceFiles, WorkspaceFull, rel_path
from ..filestore.protocol import FileNotFound
from ..sandbox.protocol import ExecResult
from .context import AgentToolContext
from .output_cap import cap_tool_outputs, truncate_middle
from .tool_authz import authorize_tool

if TYPE_CHECKING:
    from ..resources.conversation import Citation

_LOGGER = logging.getLogger(__name__)


# The head+tail truncator lives with the toolset-wide ceiling now (#44 kept its
# shape; `output_cap` owns it so the backstop and the per-tool caps cut alike).
_truncate_middle = truncate_middle


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
    # #538: NOT a fallback to `WorkspaceFiles(ctx.context.filestore)`. That
    # facade would carry no quota and no sandbox, so every write through it
    # would bypass the workspace cap and land straight in the durable store.
    # Every production context injects the app's one gated facade; a context
    # that reaches a file tool without it is a wiring bug, and failing here is
    # how it stays a bug instead of becoming a hole. `files` stays optional on
    # the context itself because the retrieval-only contexts (wiki chunk/merge,
    # the card drafter) legitimately have no file tools at all.
    assert files is not None
    assert inv is not None
    return files, inv


async def exec_impl(ctx: RunContextWrapper[AgentToolContext], cmd: list[str]) -> str:
    """Run a shell command inside the workspace sandbox. This is the only thing
    that wakes a cold sandbox: ensure_sandbox creates it and restores the
    snapshot into it, so any file writes the agent made while cold are present;
    from here on the sandbox IS the source of truth and the file tools route to
    it directly (no flush needed)."""
    if (denied := authorize_tool(ctx.context, "execute")) is not None:
        return denied
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
    if (denied := authorize_tool(ctx.context, "read_content")) is not None:
        return denied
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
) -> str | ToolOutputImage:
    """Look at an image file in the workspace (screenshot, chart, photo, or
    diagram) and reason about it.

    Use this for images — `read_file` returns raw bytes for these and is
    useless. `path` is the workspace path to the image. `question` optionally
    focuses the read (e.g. "what error is in this screenshot?"); omit it for a
    full description of everything visible.
    """
    if (denied := authorize_tool(ctx.context, "read_content")) is not None:
        return denied
    ac = ctx.context.agent_config
    # When the MAIN agent is itself a VLM, it reads the pixels directly (below);
    # a text-only main model instead needs the separate `kb.vlm_llm` describer to
    # turn the image into words — and can't read images at all without one.
    vision = ac is not None and ac.vision
    describer = ctx.context.describer
    if not vision and describer is None:
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

    if vision:
        # Hand the raw image straight to the vision-capable main model: it sees
        # the pixels itself — no main→VLM→main round-trip, no lossy image→text
        # step. The SDK renders a `ToolOutputImage` as an `input_image` part in
        # the tool-result message (the LiteLLM path preserves non-text tool
        # output), and `question` is already in the model's own context.
        b64 = base64.b64encode(data).decode("ascii")
        return ToolOutputImage(image_url=f"data:{mime};base64,{b64}")

    assert describer is not None  # the text-only path is guarded above
    sink = ctx.context.on_exec_output
    on_chunk = (lambda t, _r: sink(t.encode("utf-8"))) if sink is not None else None
    if question:
        out = describer.answer(data, mime, question=question, on_chunk=on_chunk)
    else:
        out = describer.describe(data, mime, on_chunk=on_chunk)
    return _truncate_middle(out, ctx.context.read_file_max_chars)


async def make_deck_impl(
    ctx: RunContextWrapper[AgentToolContext],
    goal: str,
    audience: str | None = None,
    source: list[str] | None = None,
    notes: str | None = None,
    style: str | None = None,
    length: str | None = None,
    out_path: str = "./deck.pptx",
) -> str:
    """Build a visually designed PowerPoint deck (`.pptx`) and write it to the
    workspace. Hand off the high-level intent — a specialist sub-agent plans the
    slides, writes the layout code, renders it, looks at the result, and fixes
    any visual problems before returning. Use this for any "make slides / a deck
    / a presentation / a one-pager" request; do NOT hand-write pptx code yourself.

    - `goal`: what the deck must convey and what it's for (be specific).
    - `audience`: who it's for (e.g. "process engineers", "executives") — optional.
    - `source`: workspace file paths to base the deck on (a report `.md`, a CSV,
      chart `.png`s). Their text is read in; images are used as figures. Optional.
    - `notes` / `style` / `length`: extra guidance — key points, brand/tone,
      "one-pager" vs "full deck". All optional.
    - `out_path`: where to write it (default `deck.pptx`).

    Returns the path written plus a short note, or an `error:` line if the deck
    tool isn't configured. Building runs several render+review passes, so it
    takes a while; its progress streams as it works.
    """
    if (denied := authorize_tool(ctx.context, "execute")) is not None:
        return denied
    from .deck.tool import run_make_deck

    fs, inv = _workspace(ctx)
    sink = ctx.context.on_exec_output

    async def write_text(path: str, content: str) -> None:
        await fs.write(inv, path, content.encode("utf-8"))

    async def read_bytes(path: str) -> bytes:
        return await fs.read(inv, path)

    async def list_dir(prefix: str) -> list[str]:
        return await fs.ls(inv, prefix)

    async def exec_run(cmd: list[str]) -> tuple[int, str]:
        handle = await ctx.context.ensure_sandbox()
        assert ctx.context.sandbox is not None
        result = await ctx.context.sandbox.exec(handle, cmd, on_output=sink)
        return result.exit_code, (result.stdout + result.stderr).decode("utf-8", errors="replace")

    progress = (lambda text: sink(text.encode("utf-8"))) if sink is not None else None
    return await run_make_deck(
        vlm=ctx.context.deck_vlm,
        write_text=write_text,
        read_bytes=read_bytes,
        list_dir=list_dir,
        exec_run=exec_run,
        progress=progress,
        goal=goal,
        audience=audience,
        source=source,
        notes=notes,
        style=style,
        length=length,
        # Relative — this string is interpolated into the JS the deck sub-agent
        # writes (`pptx.writeFile({fileName: …})`) and passed to the render
        # script, both of which run as real processes whose `/` is the SYSTEM
        # root. A rooted out_path would write the deck outside the workspace.
        out_path=rel_path(out_path).removeprefix("./") or "deck.pptx",
    )


def _guard_workspace_full(impl: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool impl so a full workspace reaches the model as instructions.

    Applied once where tools are built rather than at each write, so it covers
    every tool that can reach the store — `write_file` and `edit_file`, but
    equally `make_deck`, `save_skill`, `infer_modules`, `create_entity`, and
    whatever is added next. Left to individual `try`/`except`s this was already
    wrong for four of them: `WorkspaceFull` reached the SDK's default handler,
    the model was told only "an error occurred", and it retried the same write.

    Not every impl is a coroutine — `kb_search`, `lookup_glossary`,
    `resolve_collection` and the context-card pair are plain functions — so the
    wrapper has to preserve each one's calling convention. Wrapping a sync impl
    in an `async def` would hand `function_tool` a coroutine where it expects a
    value and break tools that never touch the workspace at all."""
    if inspect.iscoroutinefunction(impl):

        @functools.wraps(impl)
        async def _guarded_async(*args: Any, **kwargs: Any) -> Any:
            try:
                return await impl(*args, **kwargs)
            except WorkspaceFull as exc:
                return _workspace_full_msg(exc)

        return _guarded_async

    @functools.wraps(impl)
    def _guarded_sync(*args: Any, **kwargs: Any) -> Any:
        try:
            return impl(*args, **kwargs)
        except WorkspaceFull as exc:
            return _workspace_full_msg(exc)

    return _guarded_sync


def _workspace_full_msg(exc: WorkspaceFull) -> str:
    """#538: what the agent is told when a write is refused for space. The agent
    can't make more room appear, so the message names the ONE action that helps
    and the tool that does it — otherwise a model retries the same write, or
    invents a workaround like writing somewhere else."""
    return (
        f"error: the workspace is full ({exc.used} of {exc.quota} bytes used) — "
        f"writing {exc.attempted} more bytes would exceed it. Delete files that are "
        f"no longer needed with delete_file, then retry. Tell the user what you "
        f"deleted, or ask them which files they want to keep."
    )


def _conflict_echo(ctx: RunContextWrapper[AgentToolContext], path: str, current: str) -> str:
    """The current content a rejected write/edit hands back so the agent can
    retry — capped like any other tool output.

    The echo exists to make the retry possible, so cutting it costs something
    real: `old_string` has to match EXACTLY, and a truncated echo can't be
    copied from. That's why the marker names the way back (`read_file` with
    offset/limit) instead of leaving the agent to guess why its next edit also
    failed. Uncapped it was the widest hole in the toolset — the size of the
    echo is whatever the user uploaded, and a missed match on a big file is an
    everyday event."""
    return truncate_middle(
        current,
        ctx.context.exec_output_max_chars,
        hint=(
            f"this echo is partial — read_file {rel_path(path)} (offset/limit) to see the exact "
            "text you need `old_string` to match"
        ),
    )


async def write_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str, content: str) -> str:
    """Create a NEW file. This never overwrites: if the file already exists it
    is rejected and the current content is returned — use `edit_file` to change
    an existing file (so you always state what you expect to replace). This is
    what stops blind writes."""
    if (denied := authorize_tool(ctx.context, "edit_content")) is not None:
        return denied
    fs, inv = _workspace(ctx)
    current = await fs.create(inv, path, content.encode("utf-8"))
    if current is None:
        return f"wrote {len(content)} bytes to {path}"
    return (
        f"error: {path} already exists — use edit_file to modify it (or delete "
        f"it first). Current content:\n"
        f"{_conflict_echo(ctx, path, current.decode('utf-8', errors='replace'))}"
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
    if (denied := authorize_tool(ctx.context, "edit_content")) is not None:
        return denied
    fs, inv = _workspace(ctx)
    current = await fs.edit(inv, path, old_string, new_string)
    if current is None:
        return f"edited {path}"
    return (
        f"error: could not apply the edit to {path} — `old_string` was not found "
        f"exactly once (the file may have changed). Current content:\n"
        f"{_conflict_echo(ctx, path, current)}"
    )


def _capped_listing(entries: list[str], cap: int, *, offset: int = 1, noun: str, hint: str) -> str:
    """Render the window of `entries` starting at 1-based `offset`, one per
    line, stopping at `cap` characters and saying how many there were in total.

    A listing tool's answer is as big as whatever it is listing, so it needs a
    ceiling of its own — and a cut listing has to SAY it was cut, with the true
    count and the offset that resumes it. An agent that silently receives half
    a directory believes it has seen the whole thing and reasons from an
    absence that isn't real; an agent that is told it saw half, with no way to
    ask for the other half, is merely stuck."""
    start = max(offset, 1) - 1
    window = entries[start:]
    kept: list[str] = []
    used = 0
    for entry in window:
        used += len(entry) + 1
        # Always keep one: a notice with no entry at all teaches the agent
        # nothing and leaves it nothing to narrow with.
        if used > cap and kept:
            break
        kept.append(entry)
    if start == 0 and len(kept) == len(entries):
        return "\n".join(entries)
    shown_to = start + len(kept)
    more = (
        f"pass offset={shown_to + 1} to continue, or {hint}"
        if shown_to < len(entries)
        else "this is the end of the listing"
    )
    return "\n".join(
        [*kept, f"\n[{noun} {start + 1}-{shown_to} of {len(entries)} — {more}]"],
    )


async def list_files_impl(
    ctx: RunContextWrapper[AgentToolContext], prefix: str = "", offset: int = 1
) -> str:
    """List ONE level of the workspace — like `ls`, not like `find`. Returns the
    sub-directories there (each shown with a trailing `/`) followed by the files;
    pass a sub-directory back in to look inside it. `prefix` defaults to the
    workspace root, and also accepts a file (lists just that file) or the start
    of a name (lists what begins with it). Paths come back relative to the
    workspace root (`notes.txt`, `data/`), which is the form the other file
    tools and `exec` both take. A long listing is cut with a notice — pass
    `offset` to read on from there. Use this instead of `exec(["ls", ...])`."""
    fs, inv = _workspace(ctx)
    files, dirs = await fs.list_dir(inv, prefix)
    if not files and not dirs:
        return f"no files under {_shown_prefix(prefix)}"
    # Directories first: they are how the agent gets to everything not shown, so
    # a cut that removes them turns a big directory into a dead end. And every
    # path an agent SEES is relative (#549) — the store's `/x` key is the system
    # root once it reaches `exec`, and a listing is the strongest evidence the
    # model has about what a path here looks like.
    return _capped_listing(
        [rel_path(p) for p in (*dirs, *files)],
        ctx.context.exec_output_max_chars,
        offset=offset,
        noun="entries",
        hint="list a sub-directory",
    )


def _shown_prefix(prefix: str) -> str:
    """The path `list_files` was asked about, in the form the agent should use —
    so an empty listing names something it can act on, in the one path dialect
    that works everywhere (#549)."""
    from ..files.facade import _dir_key

    key = _dir_key(prefix)
    return f"{rel_path(key)}/" if key else "the workspace root"


async def exists_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> bool:
    """Check whether a file exists in the workspace file store."""
    fs, inv = _workspace(ctx)
    return await fs.exists(inv, path)


async def delete_file_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Delete a file from the workspace file store."""
    if (denied := authorize_tool(ctx.context, "edit_content")) is not None:
        return denied
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

    files = ctx.context.files
    if files is None:
        return "error: no wiki is available in this context."
    # #506: the collections to grep. The interactive kb_chat agent spans SEVERAL
    # collections (`collection_ids`, with no single `investigation_id`), so grep each
    # one's wiki store (WikiFileStore keys pages per collection) and merge. The wiki
    # maintainer / reader keep their single-collection scope (`investigation_id`,
    # `collection_ids` empty) — unchanged behaviour.
    inv = ctx.context.investigation_id
    scopes = list(ctx.context.collection_ids) or ([inv] if inv is not None else [])

    # #506: enforce the per-turn wiki-search budget (symmetric to kb_search's).
    # `None` ⇒ unlimited, so the wiki maintainer/reader are unaffected; a capped
    # ask_knowledge_base sub-agent stops grepping once spent and is steered to
    # answer from the wiki content it already found.
    budget = ctx.context.wiki_search_budget
    if budget.exhausted:
        if budget.max_calls == 0:
            return (
                "No wiki searches are allowed for this reply. Answer now from the "
                "wiki content and context you already have; do not call search_wiki."
            )
        cap = budget.max_calls
        return (
            f"Wiki search budget exhausted for this reply ({cap} of {cap} used). "
            "Answer now using the wiki content already found; do not call search_wiki again."
        )

    try:
        pattern = compile_query(query)
    except InvalidQuery as exc:
        return f"error: invalid search {query!r}: {exc}"
    hits: list[str] = []
    multi = len(scopes) > 1  # disambiguate hits by collection only when >1 is grepped
    for cid in scopes:
        for path in sorted(await files.ls(cid)):
            try:
                data = await files.read(cid, path)
            except FileNotFound:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for m in search_text(text, pattern):
                # Same dialect as `list_files`: relative to the wiki root, so a
                # path the agent reads here can be passed straight to read_file.
                rel = rel_path(path)
                where = f"{cid}/{rel}" if multi else rel
                hits.append(f"{where}:{m.line}: {m.text}")

    budget.used += 1  # every completed grep costs one unit, even a no-match

    if not hits:
        result = f"no wiki pages match {query!r}"
    else:
        body = "\n".join(hits)
        cap = ctx.context.exec_output_max_chars
        result = _truncate_middle(body, cap) if len(body) > cap else body

    if budget.max_calls is not None:
        result += (
            f"\n\n(Wiki search budget: {budget.used} of {budget.max_calls} used, "
            f"{budget.remaining} left. Only search again for a genuinely different term.)"
        )
    return result


async def read_new_source_impl(ctx: RunContextWrapper[AgentToolContext]) -> str:
    """Read the source document that triggered this wiki-maintenance run —
    the new/changed material to fold into the wiki."""
    src = ctx.context.wiki_new_source
    if not src:
        return "error: no new source for this run"
    cap = ctx.context.exec_output_max_chars
    return _truncate_middle(src, cap) if len(src) > cap else src


async def list_sources_impl(
    ctx: RunContextWrapper[AgentToolContext], prefix: str = "", offset: int = 1
) -> str:
    """List the collection's raw source documents (read-only) so you can re-read
    or cross-reference any of them while maintaining the wiki. Pass `prefix` to
    list only the paths starting with it, and `offset` to read on from where a
    cut listing stopped; the listing reports the total so you can tell a
    narrowed view from the whole collection."""
    sources = ctx.context.wiki_sources
    if sources is None:
        return "no sources are attached to this run"
    # A collection's document count is not something the agent chose, so the
    # listing is capped the same way `list_files` is — with the true total, and
    # a filter the agent can actually steer.
    paths = [p for p in sources.list() if p.startswith(prefix)]
    if not paths:
        return f"no sources match {prefix!r}" if prefix else "this collection has no sources"
    return _capped_listing(
        paths,
        ctx.context.exec_output_max_chars,
        offset=offset,
        noun="sources",
        hint="pass a longer `prefix` to narrow the listing",
    )


_WIKI_SNIPPET_MAX = 1200  # citation snippet cap (the FE reference card excerpt)


def _coerce_source_path(path: str) -> str:
    """Recover a SOURCE path from a code-wiki CARD path (#281 P7). A small reader
    model often hands ``read_source`` the wiki page path (``files/<src>.md``)
    instead of the source path it documents; ``files/app/queue.py.md`` →
    ``app/queue.py``. Both the relative form the agent is shown (`list_files` /
    `search_wiki`) and the store's own ``/files/…`` key are accepted, so the
    fallback keeps firing on whichever the model copied. Used as a fallback, so
    a real source is always tried first — a genuine source that happens to live
    under ``files/`` resolves before this is ever consulted."""
    if (body := path.lstrip("/")).startswith("files/"):
        path = body[len("files/") :]
        if path.endswith(".md"):
            path = path[: -len(".md")]
    return path


async def read_source_impl(ctx: RunContextWrapper[AgentToolContext], path: str) -> str:
    """Read one raw source document's text by its path (read-only). Pass the
    SOURCE path (e.g. ``app/queue.py``); a code-wiki card path
    (``files/app/queue.py.md``) is also accepted and resolved to its source. Use
    it to verify a fact before writing it into a wiki page, to record a page's
    ``Sources:`` provenance, and (as the reader) to ground an answer in the
    real document — cite the returned [n].

    On a reader run the result is a numbered ``[n] <source path>: text``
    reference (so you cite claims with the matching [n], like kb_search); on a
    maintainer run it's a ``Source path: <source path>`` header followed by the
    plain text. Either way the full source path is shown so you know where the
    material lives (#485)."""
    from ..resources.kb import RetrievedPassage

    sources = ctx.context.wiki_sources
    if sources is None:
        return f"error: source not found: {path}"

    if not ctx.context.wiki_cite_sources:
        # Maintainer path: plain text for cross-referencing, prefixed with the
        # source's full path so the model knows WHERE the material lives (#485) —
        # mirrors the `Source path:` header the coordinator adds to
        # read_new_source. `resolved` is the path that actually read (the arg, or
        # a /files/<src>.md card path coerced to its source, #281 P7); for a
        # valid natural-key path it is the SourceDoc's own path.
        resolved = path
        text = sources.read(path)
        if text is None and (coerced := _coerce_source_path(path)) != path:
            text, resolved = sources.read(coerced), coerced
        if text is None:
            return f"error: source not found: {path}"
        cap = ctx.context.exec_output_max_chars
        body = _truncate_middle(text, cap) if len(text) > cap else text
        return f"Source path: {resolved}\n\n{body}"

    # Reader path: register the source as a citable passage (dedup by doc id,
    # whole-document granularity) and hand it back numbered so [n] resolves to
    # the underlying SourceDoc via parse_citations.
    ref = sources.ref(path)
    if ref is None and (coerced := _coerce_source_path(path)) != path:
        ref = sources.ref(coerced)  # #281 P7: tolerate a /files/<src>.md card path
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
    # #485: show the FULL source path (folder + name), not just the basename —
    # where a doc lives is load-bearing (e.g. `2601_report.pptx/1.png`), and two
    # sources can share a basename across folders.
    return f"[{idx + 1}] {ref.path}: {body}"


# #484: how many distinct glossary cards one kb_search may inject. A generous
# sanity backstop (turn-level dedup is the real bound), sized for a large-context
# model — see the grill decision; not a per-message knob.
_GLOSSARY_INJECT_CAP = 50


def _glossary_for_passages(ctx: AgentToolContext, passage_texts: list[str]) -> str:
    """#484: scan the passages a `kb_search` just returned for terms that have a
    glossary context card and render the authoritative definitions to append to
    the result — so the model uses the curated meaning instead of inferring one
    from the surrounding prose (the "AI just makes it up" gap).

    Deduped across the turn via `ctx.injected_card_ids`: a card already injected
    by the #106 user-message pre-scan or an earlier search is skipped, so a term
    is defined exactly once per turn. Returns "" (nothing to append) when no spec/
    collections/passages are wired or nothing matched.
    """
    spec = ctx.spec
    if spec is None or not ctx.collection_ids or not passage_texts:
        return ""
    from ..kb.context_cards import (
        card_context_block,
        cards_with_ids_for_collections,
        match_with_ids,
    )

    pairs = cards_with_ids_for_collections(spec, ctx.collection_ids)
    hits = match_with_ids("\n".join(passage_texts), pairs, cap=_GLOSSARY_INJECT_CAP)
    fresh = [(rid, card) for rid, card in hits if rid not in ctx.injected_card_ids]
    cards = [card for _, card in fresh]
    block = card_context_block(cards)
    if not block:
        return ""
    # Mark only what the block actually carried: a card the budget dropped was
    # never defined for the model, so marking it would silence that term for the
    # whole turn — including a later search that matches it alone.
    from ..kb.context_cards import shown_card_count

    ctx.injected_card_ids.update(rid for rid, _ in fresh[: shown_card_count(cards)])
    return f"\n\n{block}"


def _card_anchor_doc_ids(ctx: AgentToolContext, query: str) -> frozenset[str]:
    """#518: the documents a card matched by THIS query says are the good ones.

    The deterministic half of the card-anchored precision path: pre-scan the query
    against the collection's card keys (the same `match_with_ids` the glossary
    injection uses, so a term can't count as a hit for one and a miss for the other),
    and union the linked documents of every card that hit. `keys` are term surface
    forms, not sentences, so matching is substring-with-word-boundary — "M4 etch
    recipe?" hits the `M4` card, `M40` does not. Several cards may hit at once (key ↔
    card is many-to-many), and the union is deliberate: a second matched term should
    widen the curated evidence, never narrow it to whichever key sorts first.

    Denied documents are dropped here rather than left to collide with #308's
    exclusion downstream — an AND of "only these" and "not that" could otherwise
    empty the scope for a reason that has nothing to do with the query.

    Empty result ⇒ no anchoring (no spec, no cards, no hit, or no links). Note this
    does NOT check the ids are live: `search` reports an empty scope by returning
    nothing, and the CALLER widens. That keeps one fallback path for every reason a
    scope can come up empty — deleted, renamed, cross-collection, denied.
    """
    spec = ctx.spec
    if spec is None or not ctx.collection_ids or not query.strip():
        return frozenset()
    from ..kb.context_cards import cards_with_ids_for_collections, match_with_ids

    pairs = cards_with_ids_for_collections(spec, ctx.collection_ids)
    linked = {
        doc_id
        for _rid, card in match_with_ids(query, pairs, cap=_GLOSSARY_INJECT_CAP)
        for doc_id in card.reference_doc_ids
    }
    return frozenset(linked - ctx.exclude_doc_ids)


def kb_search_impl(
    ctx: RunContextWrapper[AgentToolContext],
    query: str,
    expand: int | None = None,
    hyde: int | None = None,
    rerank: bool | None = None,
    document: str | None = None,
    page_from: int | None = None,
    page_to: int | None = None,
    sheet: str | None = None,
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

    Your kb_search calls are limited per reply; each result reports how many
    remain — spend them only on genuinely distinct information.

    The optional `expand` / `hyde` / `rerank` knobs override the operator's
    retrieval enhancement defaults for THIS call only — set them when the
    query needs more recall (raise `expand` / `hyde`) or when a quick lookup
    doesn't need the rerank LLM round-trip. The operator's `max` clamps
    whatever you pass, so requesting `expand=99` is safe.

    To fetch by EXACT location — "analyse page 30 of report.pdf", "the Summary
    sheet of Q3.xlsx", "why X failed, per pages 30-90" — pass `document` (the
    filename) together with `page_from`/`page_to` (a single page: just
    `page_from`; a range: both bounds) or `sheet`. This narrows retrieval to that
    location AND still ranks by `query`, so pair it with a real question. A
    page/sheet filter REQUIRES `document` (a page number is meaningless without a
    file). Use the filename the user gave — its folder is optional.
    """
    from ..kb.doc_resolve import resolve_document
    from ..kb.provenance import format_location
    from ..kb.retriever import Enhancements, LocationFilter

    retriever = ctx.context.retriever
    assert retriever is not None  # kb_search implies a KB context

    # #195: enforce the per-turn search budget. Once the model has used its
    # allotment, stop running the (expensive multi-query / HyDE / rerank)
    # retriever and tell it to answer from what it already retrieved — far
    # cheaper than letting a small model re-search the same thing up to
    # max_turns, and it keeps the reply focused. `None` ⇒ unlimited.
    budget = ctx.context.kb_search_budget
    if budget.exhausted:
        cap = budget.max_calls
        if cap == 0:
            # #334 Q4: the user picked "0 searches" for this reply — never run the
            # retriever, just steer the model to answer from what it already has.
            _LOGGER.info("kb_search disabled for this reply (cap=0) for query=%r", query)
            return (
                "No knowledge-base searches are allowed for this reply. Answer the "
                "user now from the conversation and any context you already have; "
                "do not call kb_search."
            )
        _LOGGER.info("kb_search budget exhausted (%d/%d) for query=%r", cap, cap, query)
        return (
            f"Search budget exhausted for this reply ({cap} of {cap} used). "
            "Answer the user now using the passages already retrieved above; "
            "do not call kb_search again."
        )

    # #263: optional structural scope. A page/sheet filter is meaningless without
    # "which file", so it requires `document`; we resolve the filename to its
    # source-doc id within the active collections (the opaque id never touches
    # the model). Resolution failures are recoverable messages the model fixes,
    # not exceptions — and they return BEFORE the budget is spent.
    location: LocationFilter | None = None
    if document is not None or page_from is not None or page_to is not None or sheet is not None:
        if document is None:
            return (
                "To fetch by location, also pass `document` (the filename) — a page "
                "or sheet on its own doesn't say which file."
            )
        spec = ctx.context.spec
        assert spec is not None  # a KB context always wires spec
        res = resolve_document(spec, ctx.context.collection_ids, document)
        if res.status == "not_found":
            return (
                f"No document matching {document!r} in the current knowledge base — "
                "check the filename (try kb_search without a document filter first)."
            )
        if res.status == "ambiguous":
            opts = ", ".join(res.candidates)
            return f"{document!r} matches several files; pass the full path, one of: {opts}"
        location = LocationFilter(
            source_doc_id=res.doc_id, page_from=page_from, page_to=page_to, sheet=sheet
        )

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

    # Consume one unit of the budget for this run. We count BEFORE searching so
    # an empty-handed or erroring search still costs a unit — otherwise a model
    # that keeps matching nothing could loop forever re-searching.
    budget.used += 1

    lines: list[str] = []
    passage_texts: list[str] = []
    try:
        # #518 card-anchored two-stage retrieval. Stage 1 searches only inside the
        # documents a card matched by this query vouches for; stage 2 is the ordinary
        # open search. We widen whenever the scoped pass yields nothing, so every way a
        # scope can be empty — a link to a deleted or renamed doc, a doc in a collection
        # this search isn't covering, one the speaker can't read — degrades to today's
        # behaviour. Curating a card can then only ever help: at worst it costs one
        # extra scoped query, never an answer the user was entitled to.
        anchor = _card_anchor_doc_ids(ctx.context, query)

        def run(restrict: frozenset[str]):
            return retriever.search(
                query,
                ctx.context.collection_ids,
                on_progress,
                enhancements=effective,
                location=location,
                exclude_doc_ids=ctx.context.exclude_doc_ids,  # #308: per-doc override
                restrict_to_doc_ids=restrict,
            )

        passages = list(run(anchor))
        if anchor and not passages:
            _LOGGER.info("kb_search: card-anchored scope was empty, widening to an open search")
            passages = list(run(frozenset()))
        for passage in passages:
            key = (passage.document_id, passage.start, passage.end)
            idx = seen.get(key)
            if idx is None:
                idx = len(registry)
                seen[key] = idx
                registry.append(passage)
            # Issue #254: prefix the passage's source location so the model can
            # cite "p.3 §2.1" in prose, not just an opaque filename.
            loc = format_location(passage.provenance)
            where = f"{passage.filename} ({loc})" if loc else passage.filename
            lines.append(f"[{idx + 1}] {where}: {passage.text}")
            passage_texts.append(passage.text)
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

    body = "\n\n".join(lines) if lines else "No matching passages in the knowledge base."
    body += _glossary_for_passages(ctx.context, passage_texts)
    # Permission-disclosure: surface collections the speaker may see-exist but not
    # read whose content is a competitive match — so the answer says "there IS an
    # answer you can't access" instead of silently omitting it. The agent is told
    # ONLY the COUNT (never the names/content), which keeps it from confidently
    # claiming nothing exists without letting a small model hallucinate the withheld
    # content; the authoritative names + "request access" action ride out-of-band on
    # the persisted message (ctx.withheld_collection_ids → WithheldSource) that the
    # FE renders. Skipped entirely when nothing is discoverable, so a turn with no
    # read_meta-only collections pays nothing (and never needs the probe at all).
    if ctx.context.discoverable_collection_ids:
        disclosed = retriever.probe_withheld(
            query, ctx.context.collection_ids, ctx.context.discoverable_collection_ids
        )
        acc = ctx.context.withheld_collection_ids
        fresh = [c for c in disclosed if c not in acc]
        acc.extend(fresh)
        if fresh:
            n = len(fresh)
            body += (
                f"\n\n(Note: {n} knowledge source{'s' if n != 1 else ''} you don't have "
                "access to also appear relevant to this query. You cannot read them, but "
                "the user can see them and request access — tell the user such sources "
                "exist; do NOT guess their contents.)"
            )
    if budget.max_calls is not None:
        body += (
            f"\n\n(Search budget: {budget.used} of {budget.max_calls} used, "
            f"{budget.remaining} left. Only search again for genuinely different "
            "information.)"
        )
    return body


async def ask_knowledge_base_impl(
    ctx: RunContextWrapper[AgentToolContext], question: str, rank: int = 0
) -> str:
    """Ask the knowledge-base agent a question about the in-house documents.

    Use this ONLY when answering needs facts, procedures, or history that live
    in the in-house knowledge base rather than in the workspace files. Returns a
    synthesized answer with a Sources list. Phrase a focused question, not just
    keywords.

    `rank` (#280) picks which **priority tier** of collections to search, when
    the knowledge base is organised into tiers. Always start at `rank=0` (the
    highest-priority tier). If that answer doesn't fully resolve your question
    and the result says more tiers exist, call this tool AGAIN with the same
    question and the next `rank` (1, 2, …). Each tier is searched on its own, so
    compare the answers you get and use the best — a higher tier is a fallback,
    not automatically better. The result tells you the tier count and when there
    are no more tiers to widen to.

    Do NOT use for: greetings, small-talk, the agent's own name or identity,
    meta-questions about this assistant, or general knowledge you already know.
    For any of those, answer directly without calling this tool.
    """
    run = ctx.context.run_subagent
    assert run is not None  # the API layer wires this for RCA runs

    # Citations are bucketed by TOOL NAME (the surface that produced them), not by
    # sub-agent purpose; persist() pairs the Nth bucket entry with the Nth tool
    # message of that name. So EVERY call must append exactly one bucket entry —
    # including the early returns below — or the pairing drifts.
    bucket = ctx.context.subagent_citations.setdefault("ask_knowledge_base", [])

    tiers = ctx.context.collection_tiers
    n = len(tiers)
    if n == 0:
        # No priority tiers configured ⇒ search the whole KB (today's behaviour).
        if rank > 0:
            bucket.append([])
            return (
                f"There is no priority tier {rank} — this knowledge base isn't "
                "organised into tiers. Call ask_knowledge_base without a rank."
            )
        scope: list[str] | None = None
        banner = ""
    elif rank < 0 or rank >= n:
        bucket.append([])
        return (
            f"There is no priority tier {rank}; the lowest-priority tier is rank "
            f"{n - 1}. Answer from what you already found across the tiers."
        )
    else:
        scope = tiers[rank]
        if rank < n - 1:
            banner = (
                f"[Searched priority tier {rank} of {n}. If this doesn't fully "
                f"answer the question, call ask_knowledge_base again with "
                f"rank={rank + 1} to widen to the next tier, then compare.]\n\n"
            )
        else:
            banner = f"[Searched the lowest-priority tier (rank {rank} of {n}); no more tiers.]\n\n"

    answer, citations = await run(
        "kb_chat",
        question,
        ctx.context.on_exec_output,
        ctx.context.investigation_id,
        scope,
        # Permission-disclosure: the KB sub-agent surfaces read_meta-only sources
        # into this turn's accumulator, so the parent's assistant message can chip
        # "there IS an answer you can't read". Keyword (not positional) so it never
        # collides with the bridge's positional args.
        withheld_sink=ctx.context.withheld_collection_ids,
    )
    bucket.append(citations)
    return banner + answer


async def ask_wiki_impl(ctx: RunContextWrapper[AgentToolContext], question: str) -> str:
    """Ask the wiki what it knows about something.

    The wiki is this knowledge base's encyclopedia: pages that consolidate what
    many documents say about one entity or concept, cross-linked and kept current
    as documents arrive. Reach for it when the question is about **understanding**
    — what something is, how it relates to other things, the shape of a topic,
    the background you would want before opening any single document.

    A wiki reader works through it for you — the index, then the pages the index
    points at, then the source documents behind those pages — and returns a
    written answer with `[n]` markers citing the underlying documents. Ask one
    focused question in natural language; it is a reader, not a search box.

    Prefer this over `kb_search` for anything conceptual or broad: the wiki has
    already done the cross-document synthesis, so one call here often replaces
    several searches. Use `kb_search` instead when you need an exact figure, a
    specific step, or the verbatim wording of one particular document.
    """
    from ..kb.citations import parse_citations, shift_markers

    # One bucket entry per call, early returns included — persist() pairs the Nth
    # entry with the Nth `ask_wiki` tool message, so a skipped append drifts every
    # later pairing (same contract as ask_knowledge_base above).
    bucket = ctx.context.subagent_citations.setdefault("ask_wiki", [])

    consult = ctx.context.run_wiki_reader
    if consult is None:
        bucket.append([])
        return (
            "There is no wiki in scope for this conversation, so there is nothing "
            "to consult. Answer from the documents and what you already have."
        )

    budget = ctx.context.wiki_search_budget
    if budget.exhausted:
        bucket.append([])
        cap = budget.max_calls
        return (
            f"Wiki budget spent for this reply ({cap} of {cap} used). Answer now "
            "from what the wiki already told you; do not call ask_wiki again."
        )

    answer, passages = await consult(question, ctx.context.on_exec_output)
    budget.used += 1  # every completed consultation costs one, answer or not

    # The reader numbered its sources from [1]; move them to their slice of THIS
    # turn's registry before appending, so the caller can quote them verbatim and
    # every marker still resolves to the document it was written against.
    shifted = shift_markers(answer, len(ctx.context.kb_passages))
    ctx.context.kb_passages.extend(passages)
    bucket.append(parse_citations(shifted, ctx.context.kb_passages))

    if budget.max_calls is not None:
        shifted += (
            f"\n\n(Wiki budget: {budget.used} of {budget.max_calls} used, "
            f"{budget.remaining} left. Consult again only for a genuinely "
            "different question.)"
        )
    return shifted


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
        # The delete comes first, so the replacement can only be smaller than
        # what it frees — the quota cannot refuse it.
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


async def lookup_user_impl(ctx: RunContextWrapper[AgentToolContext], handle: str) -> str:
    """Look up a teammate in this shared workspace by their handle.

    Each earlier user message is prefixed with `[Name (handle)]:`. Pass the
    `handle` (the part in parentheses) of someone you need to act on — this
    resolves them to their canonical id plus section / email, e.g. so you can
    `mention_user` them. Returns a single line; an unrecognised handle returns
    a short note instead.
    """
    from ..users.labels import display_handle

    users = ctx.context.users
    if users is None:
        return "error: lookup_user is only available in a shared workspace turn"
    user = users.find_by_handle(handle)
    if user is None:
        return (
            f"No teammate with handle '{handle}'. Use the handle shown in "
            "parentheses after a name in the [Name (handle)]: message prefixes."
        )
    parts = [f"handle {display_handle(user)}", f"id {user.id}"]
    if user.section:
        parts.append(f"section {user.section}")
    if user.email:
        parts.append(f"email {user.email}")
    return f"{user.name} — " + ", ".join(parts)


async def read_skill_impl(ctx: RunContextWrapper[AgentToolContext], name: str) -> str:
    """Load a skill's body markdown by name. Progressive disclosure: the
    system prompt's "Available skills" index already lists `(name,
    description)`; this tool returns the *body* on demand so large
    methodologies don't bloat every turn.

    Returns a friendly error string (not raise) for the agent to recover
    from — unknown name lists the available skills, body-cap exceeded
    explains the deployer should split the skill. Host-side only: never
    wakes the sandbox (skills are pure host markdown)."""
    from ..apps.skills import (
        SkillError,
        load_skill,
        load_workspace_skill,
        merged_profile_skills,
        workspace_skill_metas,
    )

    # #380: a skill the item toggled OFF (skill_prefs False) is not readable —
    # it's also absent from the advertised index, so refusing here is defense in
    # depth against a model that guessed the name. A skill *applied this turn*
    # overrides the toggle (its body is preloaded anyway), so it stays readable.
    if ctx.context.skill_prefs.get(name) is False and name not in ctx.context.applied_skills:
        return (
            f"error: skill {name!r} is turned off for this item. "
            f"Enable it in the skills picker to use it."
        )

    # #298: a user+AI co-created skill in this workspace shadows any package
    # skill of the same name. Read live (uncached) — it may have just been saved.
    files = ctx.context.files
    inv = ctx.context.investigation_id
    if files is not None and inv is not None:
        try:
            body = await load_workspace_skill(files, inv, name)
        except SkillError as e:
            return f"error: {e}"
        if body is not None:
            return body

    # #298 Q7: a built-in (shared) skill the App opted into — author-skill etc.
    from ..apps.shared_skills import SHARED_SKILLS, load_shared_skill
    from ..apps.skills import augment_shared_skill_body

    if name in SHARED_SKILLS:
        try:
            # plan §3.2: author-workflow's static body is purpose-only; append the
            # machine-derived DSL grammar + this app's capability/tool boundaries.
            return augment_shared_skill_body(
                name,
                load_shared_skill(name),
                ctx.context.app_slug,
                ctx.context.template_profile,
            )
        except SkillError as e:
            return f"error: {e}"

    slug = ctx.context.app_slug
    profile = ctx.context.template_profile
    if slug is None or profile is None:
        # No package profile, but a workspace might still hold skills to list.
        ws = (
            [m.name for m in await workspace_skill_metas(files, inv)]
            if files is not None and inv is not None
            else []
        )
        if ws:
            return f"error: unknown skill {name!r}. available skills: {', '.join(ws)}"
        return "error: read_skill is only available in an App workspace turn"
    try:
        return load_skill(slug, profile, name)
    except SkillError as e:
        declared = _declared_shared_skills(slug)
        names = [m.name for m in merged_profile_skills(slug, profile, declared)]
        if files is not None and inv is not None:
            names += [m.name for m in await workspace_skill_metas(files, inv)]
        avail = ", ".join(sorted(set(names))) or "(none)"
        return f"error: {e}. available skills: {avail}"


async def save_skill_impl(
    ctx: RunContextWrapper[AgentToolContext], name: str, description: str, body: str
) -> str:
    """Save a reusable skill into THIS workspace so you (and the user) can load it
    later with `read_skill`. Use this once the user has approved a skill you drafted
    together — it captures a repeatable procedure, the terminology, and the preferred
    output style for a kind of task.

    `name` is a short title (it's slugified to kebab-case — e.g. "SMT Reflow Triage"
    → `smt-reflow-triage`); `description` is a one-line "when to use this" shown in the
    skill index; `body` is the methodology in markdown. This owns the file format, so
    you only supply these three fields — it can't be silently dropped by a bad
    frontmatter. Re-saving the same name overwrites (refine freely). For a skill that
    needs reference docs or scripts, write them with `write_file` into the same
    `.skill/<name>/` folder (e.g. `.skill/<name>/references/…`, `.skill/<name>/scripts/…`)
    and point to them from the body. Returns a confirmation or an `error:` note."""
    from ..apps.skills import (
        SKILL_BODY_CAP,
        WORKSPACE_SKILL_DIR,
        render_skill_md,
        slugify_skill_name,
    )

    files = ctx.context.files
    inv = ctx.context.investigation_id
    if files is None or inv is None:
        return "error: save_skill needs a workspace (none on this turn)"
    slug = slugify_skill_name(name)
    if not slug:
        return f"error: {name!r} has no letters or digits to make a skill name from"
    if len(body) > SKILL_BODY_CAP:
        return (
            f"error: skill body is {len(body)} chars, over the {SKILL_BODY_CAP} cap — "
            "split it into smaller skills"
        )
    path = f"/{WORKSPACE_SKILL_DIR}/{slug}/SKILL.md"
    await files.write(inv, path, render_skill_md(slug, description, body).encode("utf-8"))
    return (
        f"saved skill '{slug}' to {rel_path(path)}. Load it any time with "
        f"read_skill('{slug}'). "
        "To reuse it elsewhere, download the .skill folder from the Skills panel."
    )


def _profile_tool_ceiling(app_slug: str | None, profile: str | None) -> set[str] | None:
    """The tools an agent in this App profile may hold — the App's ``agent.tools`` ceiling,
    narrowed by the profile's ``tools`` override (#323, Q4: a workflow's agent steps can't
    exceed what its author could use by hand). ``None`` (skip the clamp) for a synthetic /
    unreadable slug."""
    if app_slug is None or profile is None:
        return None
    from msgspec import UNSET

    from ..apps.manifest import load_app_manifest
    from ..apps.profiles import load_profile

    try:
        app_tools = set(load_app_manifest(app_slug).agent.tools)
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None
    pm_tools = load_profile(app_slug, profile).tools
    return (set(pm_tools) & app_tools) if pm_tools is not UNSET else app_tools


async def save_workflow_impl(
    ctx: RunContextWrapper[AgentToolContext], id: str, workflow_json: str
) -> str:
    """Save a workflow you co-designed with the user into THIS workspace so it can be run,
    downloaded to hand off, or promoted to a default. A workflow is DATA, not code (#323):
    a small ordered set of steps — `agent` (an LLM turn), `sandbox` (a command), `gate` (a
    human approval), `capability` (file into a collection / write a context card), and
    `map` (repeat over uploaded files) — that the platform interprets, so it's safe to run.
    The `author-workflow` skill walks you through drafting one with the user.

    `id` is a short name (kebab-cased to the filename + id); `workflow_json` is the full
    workflow.json text. This VALIDATES it before saving — if a step type, phase, capability,
    check, or `{variable}` is off, it returns the problems so you can fix and re-save (don't
    guess; address each one). Re-saving the same id overwrites. On success the user can Run
    it, or download `.workflows/` to reuse elsewhere."""
    from ..workflow.workspace_store import (
        save_workspace_workflow,
        slugify_workflow_id,
        validate_workflow_json,
    )

    files = ctx.context.files
    inv = ctx.context.investigation_id
    if files is None or inv is None:
        return "error: save_workflow needs a workspace (none on this turn)"
    slug = slugify_workflow_id(id)
    if not slug:
        return f"error: {id!r} has no letters or digits to make a workflow id from"
    ceiling = _profile_tool_ceiling(ctx.context.app_slug, ctx.context.template_profile)
    workflow, errs = validate_workflow_json(workflow_json, tool_ceiling=ceiling)
    if workflow is None or errs:
        return "error: the workflow has problems — fix these and save again:\n- " + "\n- ".join(
            errs
        )
    path = await save_workspace_workflow(files, inv, slug, workflow)
    return (
        f"saved workflow '{slug}' to {rel_path(path)}. The user can Run it from this "
        "item, or download "
        "the .workflows folder from the Workflows panel to reuse or hand it to the dev team."
    )


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
    instead.

    `keys` are how the card is found: each is matched by EXACT membership after normalisation,
    so a query must equal a WHOLE key. Keep EVERY surface form a reader might type (its
    abbreviation, full name, an English/Chinese alias) as its OWN short key — since this
    overwrites, dropping an existing alias means the card stops matching it. Write `body` in
    markdown. Returns a confirmation, or an `error:` note (re-read and retry on a clash)."""
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
    first). `collection` is an id or name.

    `keys` are how the card is later found: each key is matched by EXACT membership after
    normalisation (case-folded, full/half-width unified, whitespace collapsed), so a query
    must equal a WHOLE key — 'M4' never finds an 'M40' card. List every surface form a reader
    might type (abbreviation, full name, an English/Chinese alias) as its OWN key, each a
    short term or phrase not a sentence; case/width variants already normalise together so
    don't repeat them. Write `body` in markdown (bold / lists / `code`; the title already
    shows). Returns a confirmation or an `error:` note."""
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


def _wiki_enabled(spec, collection_id: str) -> bool:
    """Whether ``collection_id`` exists and has its LLM wiki on (#397) — the tool
    only offers to correct collections that actually have a wiki."""
    from specstar.types import ResourceIDNotFoundError

    from ..resources.kb import Collection

    try:
        coll = spec.get_resource_manager(Collection).get(collection_id).data
    except ResourceIDNotFoundError:
        return False
    return isinstance(coll, Collection) and coll.use_wiki


async def request_wiki_update_impl(
    ctx: RunContextWrapper[AgentToolContext],
    instruction: str,
    target_page: str = "",
    reference: str = "",
    collection: str = "",
) -> str:
    """Ask the wiki maintainer to CORRECT the knowledge wiki (#397) — use this
    instead of editing wiki pages yourself. Describe what is wrong and how it
    should read; the maintainer then finds and fixes the affected page(s). The
    corrected fact is recorded so a later rebuild can't reintroduce the error.

    `instruction` (required): the correction in plain language — what the wiki
    currently gets wrong and what it should say. `target_page` (optional): the
    wiki page path at fault (e.g. `/entities/foo.md`) if you know it; leave blank
    to let the maintainer locate it. `reference` (optional): a short reference
    document or passage to follow for the fix — its text is used for THIS
    correction only. `collection` (optional): the collection to correct, by name
    or id — required only when more than one wiki-enabled collection is in scope.

    Returns a confirmation, or an `error:` note (e.g. no wiki to correct). The fix
    runs in the background; you don't wait for it."""
    from ..kb.wiki.corrections import WikiNotEnabledError

    submit = ctx.context.submit_wiki_correction
    if submit is None:
        return (
            "error: request_wiki_update isn't available here (no wiki-enabled collection in scope)"
        )
    spec = ctx.context.spec
    if spec is None:
        return "error: request_wiki_update needs a collection-scoped context (no spec on this turn)"
    if not instruction.strip():
        return "error: describe what's wrong in `instruction` — it can't be empty"

    in_scope = ctx.context.collection_ids
    if collection.strip():
        from ..workflow.capabilities import CollectionNotFound, resolve_collection_id

        try:
            cid = resolve_collection_id(spec, collection)
        except CollectionNotFound:
            return f"error: unknown collection {collection!r}"
        if cid not in in_scope:
            return f"error: collection {collection!r} isn't in scope for this chat"
    else:
        wiki_cids = [c for c in in_scope if _wiki_enabled(spec, c)]
        if not wiki_cids:
            return "error: no wiki-enabled collection is in scope to correct"
        if len(wiki_cids) > 1:
            return (
                "error: more than one wiki-enabled collection is in scope — pass `collection` "
                "(a name or id) to say which wiki to correct"
            )
        cid = wiki_cids[0]

    try:
        await submit(
            cid,
            instruction=instruction,
            target_page=target_page,
            reference=reference,
            requested_by=ctx.context.acting_user,
        )
    except WikiNotEnabledError:
        return "error: that collection has no wiki to correct"
    return (
        "Submitted your wiki correction. It's on record and the wiki is being "
        "updated to reflect it."
    )


# ── entity tools (#419) ──────────────────────────────────────────────────────
# The agent's write path into the file-first entity framework — the same
# `EntityStore` pipeline the quick-create UI and workflows use (permanent
# numbering + skeleton render + lint), so an AI-authored issue/milestone is
# indistinguishable from a UI-authored one. Schema-agnostic: `type_name` selects
# the entity type the workspace declares under `.entity/`, so one set of tools
# serves any app's entities.

# Process-wide numbering locks, keyed `"{workspace}:{type}"` — so two entity
# creates racing across turns on one item can't claim the same number (mirrors
# the API's shared lock registry; single-pod serialization, §N5).
_ENTITY_LOCKS: dict[str, asyncio.Lock] = {}


async def _entity_store(ctx: RunContextWrapper[AgentToolContext], type_name: str):
    """`(store, error)` — build an `EntityStore` for this item and confirm the
    type exists. On an unknown/absent type, `store` is None and `error` is a
    ready-to-return message listing the declared types."""
    from ..entity.catalog import discover_catalog
    from ..entity.store import EntityStore

    files, ws = _workspace(ctx)
    catalog, _diags = await discover_catalog(files, ws)
    if type_name not in catalog:
        declared = ", ".join(catalog.names()) or "none"
        return (
            None,
            f"error: unknown entity type {type_name!r} (this workspace declares: {declared})",
        )
    return (
        EntityStore(
            files, ws, catalog, locks=_ENTITY_LOCKS, on_write=ctx.context.entity_write_sink
        ),
        None,
    )


def _entity_diag_suffix(entity) -> str:
    """A trailing ` Warnings: …` note for any lint diagnostics (§C7 lint-not-block
    — the write still lands, the agent just hears what looked off)."""
    if not entity.diagnostics:
        return ""
    return " Warnings: " + "; ".join(d.message for d in entity.diagnostics)


async def create_entity_impl(
    ctx: RunContextWrapper[AgentToolContext], type_name: str, args: dict[str, Any]
) -> str:
    """Create a structured record (an issue, a milestone, …) in this workspace.

    `type_name` is the record type the workspace declares (call query_entity or
    read `.entity/` to see which). `args` fills the type's create fields by name
    (the same fields the quick-create form shows) — e.g.
    `{"title": "Login broken", "status": "open"}`. The record gets the next
    permanent number automatically; reference it later by that number. Returns
    the new record's number (and any lint warnings)."""
    store, err = await _entity_store(ctx, type_name)
    if store is None:
        return err
    from datetime import UTC, datetime

    created = await store.create(
        type_name,
        args,
        actor=ctx.context.acting_user,
        now=datetime.now(UTC).date().isoformat(),
        origin=ctx.context.entity_write_origin,
    )
    return f"Created {type_name} #{created.number}.{_entity_diag_suffix(created)}"


async def update_entity_impl(
    ctx: RunContextWrapper[AgentToolContext],
    type_name: str,
    number: int,
    patch: dict[str, Any],
    expected_version: str = "",
) -> str:
    """Change fields on an existing record. `patch` carries only the fields to
    change (others keep their current value) — e.g. `{"status": "done"}` or
    `{"progress": 60}`. Identify the record by its `type_name` + `number`. Pass
    `expected_version` (the `version` query_entity reported for the record) to be
    told, instead of silently overwriting, if the record changed since you read
    it — then re-read and retry. Returns a confirmation (and any lint warnings)."""
    from ..entity.store import EntityConflict

    store, err = await _entity_store(ctx, type_name)
    if store is None:
        return err
    try:
        updated = await store.update(
            type_name,
            number,
            patch,
            expected_version=expected_version or None,
            actor=ctx.context.acting_user,
            origin=ctx.context.entity_write_origin,
        )
    except FileNotFound:
        return f"error: no {type_name} #{number} in this workspace"
    except EntityConflict as e:
        return f"error: {e} — re-read it with query_entity and retry"
    return f"Updated {type_name} #{number}.{_entity_diag_suffix(updated)}"


# One page of records, when the caller doesn't say. Sized so a typical page is
# a few thousand characters; the character budget below is what actually binds
# when records are wide rather than numerous.
_ENTITY_PAGE_DEFAULT = 50


async def query_entity_impl(
    ctx: RunContextWrapper[AgentToolContext],
    type_name: str,
    offset: int = 1,
    limit: int | None = None,
) -> str:
    """List the records of a type with their fields (relational fields like
    back-references and roll-ups are resolved for you). Use this to see what
    exists before creating or updating. Reads ONE page: `offset` is the 1-based
    first record (default 1) and `limit` the number of records (default: the
    configured page). Returns JSON: `entities` (the readable records), `total`
    (how many records the type has), `invalid` (numbers of records whose file
    couldn't be parsed, itself a page — `invalid_total` is how many there are),
    and `next_offset` when more records remain."""
    store, err = await _entity_store(ctx, type_name)
    if store is None:
        return err
    result = await store.query(type_name)
    start = max(offset, 1) - 1
    # A page of zero records whose `next_offset` points back at itself is a loop
    # with no exit, and the generated schema REQUIRES the model to emit `limit`
    # with no default — so a small model answering 0 is an ordinary failure, not
    # an abuse. Clamp rather than trust (a negative would slice from the end).
    count = max(limit, 1) if limit is not None else _ENTITY_PAGE_DEFAULT
    # A page is bounded twice: by record count, and by the characters those
    # records render to. Neither alone is enough — 2000 records overflow a
    # context by being many, five records with a pasted log in a field overflow
    # it by being wide. `total` is always the true count, so a paged answer can
    # never read as "this is everything".
    budget = ctx.context.exec_output_max_chars
    entities: list[dict[str, Any]] = []
    used = 0
    for entity in result.entities[start : start + count]:
        record = {"number": entity.number, "version": entity.version, "fields": entity.fields}
        used += len(json.dumps(record, ensure_ascii=False, default=str))
        if used > budget and entities:
            break
        entities.append(record)
    payload: dict[str, Any] = {
        "entities": entities,
        "total": len(result.entities),
        # `invalid` grows with the store exactly like `entities` does — a
        # workspace where a bad template broke every record would otherwise
        # dump every number from the one tool that pages.
        "invalid": [e.number for e in result.invalid[:_ENTITY_PAGE_DEFAULT]],
        "invalid_total": len(result.invalid),
    }
    if start + len(entities) < len(result.entities):
        payload["next_offset"] = start + len(entities) + 1
    return json.dumps(payload, ensure_ascii=False, default=str)


async def link_entity_impl(
    ctx: RunContextWrapper[AgentToolContext],
    type_name: str,
    number: int,
    field: str,
    target: int,
) -> str:
    """Point one record's reference field at another record. E.g. attach issue #3
    to milestone #1 with `type_name="issue", number=3, field="milestone",
    target=1`. `field` is the reference field on `type_name`; `target` is the
    referenced record's number. Returns a confirmation."""
    store, err = await _entity_store(ctx, type_name)
    if store is None:
        return err
    try:
        await store.update(
            type_name,
            number,
            {field: target},
            actor=ctx.context.acting_user,
            origin=ctx.context.entity_write_origin,
        )
    except FileNotFound:
        return f"error: no {type_name} #{number} in this workspace"
    return f"Linked {type_name} #{number} {field} → #{target}."


_IMPLS = {
    "exec": exec_impl,
    "read_file": read_file_impl,
    "read_image": read_image_impl,
    "make_deck": make_deck_impl,
    "write_file": write_file_impl,
    "edit_file": edit_file_impl,
    "list_files": list_files_impl,
    "exists": exists_impl,
    "delete_file": delete_file_impl,
    "mention_user": mention_user_impl,
    "lookup_user": lookup_user_impl,
    "ask_knowledge_base": ask_knowledge_base_impl,
    "infer_modules": infer_modules_impl,
    "kb_search": kb_search_impl,
    # #537: the KB agent's wiki entry point. Delegating like ask_knowledge_base —
    # the index-first navigation runs in a throwaway context and only the answer
    # comes back — NOT a leaf like `search_wiki`, which is why granting it to a KB
    # agent doesn't recurse: the wiki reader it spawns holds the leaves, not this.
    "ask_wiki": ask_wiki_impl,
    # Topic Hub tools — query specstar resources via ctx.spec (no retriever).
    "resolve_collection": resolve_collection_impl,
    "lookup_glossary": lookup_glossary_impl,
    "update_context_card": update_context_card_impl,
    "create_context_card": create_context_card_impl,
    # #397: submit a wiki correction (tell the maintainer what's wrong instead of
    # editing pages directly). In _WORKSPACE_TOOLS + the kb_chat presets; no-ops
    # with a friendly error on turns that scope no wiki-enabled collection.
    "request_wiki_update": request_wiki_update_impl,
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
    # `save_skill` (#298) is a normal opt-in tool — listed in an App's
    # `agent.tools` like any other (the workspace apps that ship the
    # `author-skill` meta-skill grant it). Deterministic SKILL.md write.
    "save_skill": save_skill_impl,
    # `save_workflow` (#323) — same shape: an opt-in tool the apps that ship the
    # `author-workflow` meta-skill grant. Validates + writes a workspace workflow.json.
    "save_workflow": save_workflow_impl,
    # #419 entity tools — the AI write path into the file-first entity framework
    # (same EntityStore pipeline as the quick-create UI + workflows). Opt-in per
    # app; need `function.workspace` (they touch the item's files).
    "create_entity": create_entity_impl,
    "update_entity": update_entity_impl,
    "query_entity": query_entity_impl,
    "link_entity": link_entity_impl,
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
    "list_files",
    "exists",
    "delete_file",
    "ask_knowledge_base",
    "request_wiki_update",
    "infer_modules",
    "mention_user",
    "lookup_user",
]

# Legacy tool names in a *stored* `allowed_tools` list, mapped to their current
# name so an AgentConfig persisted before a rename still provisions the tool.
# This is input normalisation only — the old name is NOT a callable alias (the
# model still calls the tool by its current registered name), it just keeps old
# config data working. #241: `ls` was renamed to `list_files`.
_LEGACY_TOOL_RENAMES = {"ls": "list_files"}


# Tools whose args include a free-form `dict[str, Any]` (entity `args` / `patch`):
# a strict JSON schema forbids the `additionalProperties` such an open object
# needs, so they build as non-strict. Entity fields are open by design (the
# schema lives in the workspace, not the tool signature), so this is correct, not
# a workaround.
_NONSTRICT_TOOLS = frozenset({"create_entity", "update_entity"})


def builtin_tool_descriptions() -> dict[str, str]:
    """Every built-in tool's registered name → its LLM-facing description (the
    impl's docstring). One source for the tool catalog (#322) so the web picker
    and chat tool cards label tools off the same text the model sees, instead of
    a hand-kept FE map that drifts. Package-command descriptions are read
    separately from the prebuilt bundles (``discover_packages``)."""
    return {
        name: (
            function_tool(
                impl, name_override=name, strict_mode=name not in _NONSTRICT_TOOLS
            ).description
            or ""
        )
        for name, impl in _IMPLS.items()
    }


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
    names = [_LEGACY_TOOL_RENAMES.get(n, n) for n in names]
    # Skip names that aren't built-ins — they may be provisioned tool-package
    # commands (#21, #25), which the runner adds separately via
    # `workspace_app.tooling.registry.build_function_tools`. The colon syntax
    # entries (`pkg:cmd`) likewise aren't built-ins and fall through here.
    tools = [
        function_tool(
            _guard_workspace_full(_IMPLS[n]),
            name_override=n,
            strict_mode=n not in _NONSTRICT_TOOLS,
        )
        for n in names
        if n in _IMPLS
    ]
    if app_slug is not None and profile is not None:
        from ..apps.skills import merged_profile_skills

        # #298: read_skill is wired when the App ships ANY skill the agent might
        # load — the profile's own package `.skill/` OR a declared shared skill
        # (e.g. author-skill). Workspace skills also need it, and a workspace app
        # that opts into author-skill always has at least that, so this covers
        # the authoring entry point too.
        if merged_profile_skills(app_slug, profile, _declared_shared_skills(app_slug)):
            tools.append(
                function_tool(
                    _guard_workspace_full(_IMPLS["read_skill"]), name_override="read_skill"
                )
            )
    # Nothing leaves here without a ceiling on what it can put in the context.
    return cap_tool_outputs(tools)


def _declared_shared_skills(app_slug: str) -> list[str]:
    """The App's opted-in shared skills (``app.json`` ``agent.skills``), or ``[]``
    when the manifest is missing/unreadable (test-synthetic slugs)."""
    from ..apps.manifest import load_app_manifest

    try:
        return list(load_app_manifest(app_slug).agent.skills)
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return []

import pytest
from agents import FunctionTool, RunContextWrapper

from workspace_app.agent import (
    AgentToolContext,
    build_tools,
    delete_file_impl,
    edit_file_impl,
    exec_impl,
    exists_impl,
    list_files_impl,
    read_file_impl,
    write_file_impl,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import (
    SandboxBusy,
    SandboxHandle,
    SandboxNotFound,
    SandboxSpec,
)
from workspace_app.sync import SandboxSync


async def test_read_file_caps_lines_with_a_notice_and_supports_offset_limit():
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1", files=files, read_file_max_lines=3, read_file_max_chars=10_000
        )
    )
    body = "\n".join(f"line{i}" for i in range(10))  # line0 .. line9
    await write_file_impl(ctx, "/big.txt", body)

    # default (no offset/limit): first max_lines lines + a truncation notice
    out = await read_file_impl(ctx, "/big.txt")
    head, _, notice = out.partition("[truncated")
    assert head.strip() == "line0\nline1\nline2"
    assert notice and "offset" in notice  # tells the agent how to read more

    # offset is 1-based; limit windows from there
    win = await read_file_impl(ctx, "/big.txt", offset=5, limit=2)
    assert win.partition("[truncated")[0].strip() == "line4\nline5"

    # a small file (under the caps) is returned verbatim — no notice
    await write_file_impl(ctx, "/small.txt", "a\nb")
    assert await read_file_impl(ctx, "/small.txt") == "a\nb"


async def test_read_file_caps_total_chars_even_on_one_long_line():
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            files=files,
            read_file_max_lines=10_000,
            read_file_max_chars=20,
        )
    )
    await write_file_impl(ctx, "/wide.txt", "x" * 100)
    out = await read_file_impl(ctx, "/wide.txt")
    assert out.startswith("x" * 20)
    assert "x" * 100 not in out
    assert "[truncated" in out


async def test_write_file_is_create_only_and_edit_file_modifies():
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files))

    assert "wrote" in await write_file_impl(ctx, "/a.txt", "hello world")
    # write_file won't clobber an existing file — it reports the conflict + content
    again = await write_file_impl(ctx, "/a.txt", "nope")
    assert "already exists" in again and "hello world" in again
    assert await read_file_impl(ctx, "/a.txt") == "hello world"

    # edit_file replaces a unique match
    assert "edited" in await edit_file_impl(ctx, "/a.txt", "world", "there")
    assert await read_file_impl(ctx, "/a.txt") == "hello there"

    # a stale/absent old_string is rejected, returning the current content
    miss = await edit_file_impl(ctx, "/a.txt", "world", "X")
    assert "could not apply" in miss and "hello there" in miss


async def test_edit_file_catches_a_concurrent_human_change():
    """The agent read a file, a human edited it, then the agent's edit (built on
    the stale text) is rejected — it re-reads and succeeds. CAS in action."""
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files))
    await write_file_impl(ctx, "/n.txt", "value = 1")

    # human edits it out-of-band (last-writer-wins, no expected)
    await files.write("inv-1", "/n.txt", b"value = 2")

    # agent tries to edit based on what it thought was there → rejected
    rejected = await edit_file_impl(ctx, "/n.txt", "value = 1", "value = 99")
    assert "could not apply" in rejected and "value = 2" in rejected
    # agent re-bases on the current content → succeeds
    assert "edited" in await edit_file_impl(ctx, "/n.txt", "value = 2", "value = 99")
    assert await read_file_impl(ctx, "/n.txt") == "value = 99"


async def test_file_tools_use_injected_files_facade():
    """When the caller injects a WorkspaceFiles facade, the file tools go
    through it (covers the non-fallback branch of _workspace)."""
    files = WorkspaceFiles(MemoryFileStore())
    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files))
    await write_file_impl(ctx, "/a.txt", "hello")
    assert await read_file_impl(ctx, "/a.txt") == "hello"
    assert await exists_impl(ctx, "/a.txt") is True


async def test_exec_creates_the_sandbox_directly_when_no_ensure_via():
    """No ensure_sandbox_via → exec creates the sandbox itself."""
    from workspace_app.sandbox.mock import MockSandbox

    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            sandbox=MockSandbox(),
            filestore=MemoryFileStore(),
            files=WorkspaceFiles(MemoryFileStore()),
        )
    )
    await write_file_impl(ctx, "/a.txt", "hi")
    assert await read_file_impl(ctx, "/a.txt") == "hi"
    assert ctx.context.handle is None
    await exec_impl(ctx, ["echo", "x"])  # ensure_sandbox direct-create branch
    assert ctx.context.handle is not None


async def test_a_file_tool_without_a_facade_fails_instead_of_bypassing_the_quota():
    """#538: the missing-facade case used to wrap the bare filestore, producing
    an unquota'd facade that wrote straight to the durable store. A context that
    reaches a file tool without the app's gated facade is a wiring bug."""
    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", filestore=MemoryFileStore()))
    with pytest.raises(AssertionError):
        await write_file_impl(ctx, "/a.txt", "hi")


class _ReapableSandbox(MockSandbox):
    """A sandbox whose handles can be taken away mid-turn, the way the host's
    idle reaper and a `rollout restart` both do."""

    def __init__(self) -> None:
        super().__init__()
        self.dead: set[str] = set()
        self.busy = False
        self.exec_handles: list[str] = []

    async def exec(self, handle, cmd, on_output=None, env=None, exec_timeout=None):
        self.exec_handles.append(handle.id)
        if self.busy:
            raise SandboxBusy(handle.id)
        if handle.id in self.dead:
            raise SandboxNotFound(handle.id)
        return await super().exec(
            handle, cmd, on_output=on_output, env=env, exec_timeout=exec_timeout
        )


def _reapable_ctx(sandbox: _ReapableSandbox) -> RunContextWrapper[AgentToolContext]:
    """Wired like `ctx`, and — the part that matters — like the REGISTRY.

    The first version of this double minted a fresh handle on every wake, which
    made the recovery look like it worked and hid the defect review found: on
    the `kind: local` backend `InvestigationRegistry.ensure_handle` re-acquires
    only when the SESSION has no handle (its liveness probe is http-only), so
    clearing the context's copy changed nothing and the retry got the same dead
    handle back. A double that always says yes cannot fail the way the real
    thing does.

    So `wake` holds a session handle and reuses it, exactly as the registry
    does, and only `rebuild` — the `force=True` path — clears it."""
    spec = make_spec(default_user="test-user")
    filestore = SpecstarFileStore(spec)
    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    holder: dict[str, SandboxHandle] = {}
    session: dict[str, SandboxHandle | None] = {"handle": None}

    async def _resolve(ws: str) -> SandboxHandle | None:
        return holder.get(ws)

    async def wake(on_progress=None, tools=None) -> SandboxHandle:
        if session["handle"] is None:
            h = await sandbox.create(SandboxSpec(tools=tools))
            await sync.restore("ws-test", h, on_progress=on_progress)
            holder["ws-test"] = h
            session["handle"] = h
        current = session["handle"]
        assert current is not None
        return current

    async def rebuild(tools=None) -> SandboxHandle:
        session["handle"] = None  # what `force=True` does to the session
        return await wake(None, tools)

    return RunContextWrapper(
        AgentToolContext(
            investigation_id="ws-test",
            sandbox=sandbox,
            filestore=filestore,
            files=WorkspaceFiles(filestore, sandbox, _resolve),
            sync=sync,
            ensure_sandbox_via=wake,
            rebuild_sandbox_via=rebuild,
        )
    )


async def test_exec_rebuilds_and_reruns_when_the_sandbox_was_reaped():
    """The host takes the sandbox away mid-turn — its 30-minute idle TTL, or a
    `rollout restart` killing every pod at once — while this turn still holds
    the handle it cached on its first exec.

    The FILE tools have recovered from exactly this since #492: `_warm` probes
    on every op and rebuilds, and its comment calls that probe "the RECOVERY
    trigger". `exec` had no such path — the exception went straight to the SDK,
    which showed the model `Error: <opaque base64 handle>` and told it to try
    again, which it did, against the same dead handle, forever."""
    sandbox = _ReapableSandbox()
    ctx = _reapable_ctx(sandbox)

    assert "hi" in await exec_impl(ctx, ["echo", "hi"])
    first = ctx.context.handle
    assert first is not None

    sandbox.dead.add(first.id)  # the host reaps it out from under the turn

    assert "back" in await exec_impl(ctx, ["echo", "back"])
    assert ctx.context.handle is not None
    assert ctx.context.handle.id != first.id, "should be running in a REBUILT sandbox"
    # Attempted on the dead handle, then once on the fresh one — one retry, not a loop.
    assert sandbox.exec_handles == [first.id, first.id, ctx.context.handle.id]


async def test_a_rebuilt_sandbox_prepares_its_python_environment_again():
    """`.venv/` is in `sync/ignore.py`'s ignores, so a rebuild brings the
    workspace back WITHOUT it. The context remembers that the environment is
    ready in `_project_env_ready`, and that belief outlived the sandbox it was
    about: the re-run — and every later exec in the turn — would have executed
    against a venv that no longer existed, silently, on the carrier python."""
    sandbox = _ReapableSandbox()
    ctx = _reapable_ctx(sandbox)
    prepared: list[str] = []
    ctx.context.prepare_env_via = lambda handle, on_output: _record(prepared, handle)

    await exec_impl(ctx, ["echo", "hi"])
    assert len(prepared) == 1
    first = ctx.context.handle
    assert first is not None

    sandbox.dead.add(first.id)
    await exec_impl(ctx, ["echo", "back"])

    assert len(prepared) == 2, "the rebuilt sandbox never had its environment prepared"
    assert prepared[1] != prepared[0], "it must be prepared for the NEW sandbox"


async def _record(sink: list[str], handle) -> None:
    sink.append(handle.id)


async def test_exec_does_not_rerun_a_command_a_busy_sandbox_may_have_already_run():
    """`SandboxBusy` is NOT the same permission to retry. The sandbox is ALIVE
    (`registry._alive` reads busy as alive on purpose, #492) and the command may
    already be running or done — a timeout on a stream says nothing about what
    the far end did. Re-sending `rm -rf`, `git push` or a POST would be this
    fix causing the damage it exists to prevent, so busy propagates and the
    command is sent exactly once."""
    sandbox = _ReapableSandbox()
    ctx = _reapable_ctx(sandbox)
    sandbox.busy = True

    with pytest.raises(SandboxBusy):
        await exec_impl(ctx, ["rm", "-rf", "/data"])

    assert len(sandbox.exec_handles) == 1, "a busy sandbox must not be re-sent the command"


async def test_exec_lazy_creates_sandbox_on_first_call(
    ctx: RunContextWrapper[AgentToolContext],
):
    assert ctx.context.handle is None
    await exec_impl(ctx, ["echo", "hi"])
    assert ctx.context.handle is not None


async def test_ensure_sandbox_hands_restore_progress_sink_to_the_wake_hook_492():
    """#492 P11: the ctx's restore-progress sink is passed to the wake hook, so a
    cold-wake restore's (done, total) ticks reach the turn's stream instead of
    leaving a blank running card."""
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.sandbox.protocol import SandboxSpec

    sandbox = MockSandbox()
    received: list[object] = []

    async def wake(on_progress=None, tools=None):
        received.append(on_progress)
        return await sandbox.create(SandboxSpec(tools=tools))

    def sink(done: int, total: int) -> None:  # a turn's restore-progress sink
        return None

    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1",
            sandbox=sandbox,
            ensure_sandbox_via=wake,
            on_restore_progress=sink,
        )
    )
    await ctx.context.ensure_sandbox()
    assert received == [sink]  # the exact sink flowed to the wake hook


async def test_no_drift_between_file_tools_and_exec():
    """P2 regression: with a liveness-routing facade, the agent's file tools and
    exec share ONE view — cold writes survive the wake, and files the shell
    creates are visible to read_file/ls (the bug the redesign fixes)."""
    from workspace_app.sandbox.mock import MockSandbox
    from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

    fs = MemoryFileStore()
    sandbox = MockSandbox()
    handle: dict[str, SandboxHandle] = {}

    async def _resolve(ws: str) -> SandboxHandle | None:
        return handle.get(ws)

    files = WorkspaceFiles(fs, sandbox, _resolve)

    async def wake(on_progress=None, tools=None) -> SandboxHandle:  # mimic ensure_handle
        h = await sandbox.create(SandboxSpec(tools=tools))
        for p in await fs.ls("inv-1"):
            await sandbox.upload(h, await fs.read("inv-1", p), p)
        handle["inv-1"] = h
        return h

    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1", sandbox=sandbox, files=files, ensure_sandbox_via=wake
        )
    )

    # (a) write while cold → lands in the snapshot; exec wakes (restores) → shell sees it
    await write_file_impl(ctx, "/x.txt", "hello")
    assert await fs.exists("inv-1", "/x.txt") is True
    assert "hello" in await exec_impl(ctx, ["cat", "/x.txt"])

    # (b) warm: a tool write goes to the sandbox, NOT the snapshot
    await write_file_impl(ctx, "/y.txt", "world")
    assert await fs.exists("inv-1", "/y.txt") is False
    assert await read_file_impl(ctx, "/y.txt") == "world"

    # (c) THE fix: a file the shell created in the sandbox is visible to read_file/list_files
    await sandbox.upload(handle["inv-1"], b"from-shell", "/z.txt")  # simulate exec output
    assert await read_file_impl(ctx, "/z.txt") == "from-shell"
    assert "z.txt" in await list_files_impl(ctx)


async def test_exec_returns_formatted_output(ctx: RunContextWrapper[AgentToolContext]):
    out = await exec_impl(ctx, ["echo", "hello"])
    assert "exit_code=0" in out
    assert "hello" in out


async def test_exec_reuses_same_sandbox_handle(ctx: RunContextWrapper[AgentToolContext]):
    await exec_impl(ctx, ["echo", "a"])
    h1 = ctx.context.handle
    await exec_impl(ctx, ["echo", "b"])
    assert ctx.context.handle is h1


async def test_exec_streams_output_to_context_sink(ctx: RunContextWrapper[AgentToolContext]):
    """exec forwards the command's stdout to the context's on_exec_output sink
    as it runs, so the runner can surface it live in run history."""
    chunks: list[bytes] = []
    ctx.context.on_exec_output = chunks.append
    await exec_impl(ctx, ["echo", "hello"])
    assert b"".join(chunks) == b"hello\n"


async def test_write_then_read_roundtrip(ctx: RunContextWrapper[AgentToolContext]):
    msg = await write_file_impl(ctx, "/notes.txt", "hello world")
    assert "wrote" in msg
    assert await read_file_impl(ctx, "/notes.txt") == "hello world"


async def test_read_missing_returns_error_string(
    ctx: RunContextWrapper[AgentToolContext],
):
    out = await read_file_impl(ctx, "/never")
    assert "error" in out.lower()


async def test_file_ops_do_not_create_sandbox(
    ctx: RunContextWrapper[AgentToolContext],
):
    await write_file_impl(ctx, "/x", "x")
    await read_file_impl(ctx, "/x")
    await list_files_impl(ctx)
    await exists_impl(ctx, "/x")
    assert ctx.context.handle is None


async def test_list_files_after_writes(ctx: RunContextWrapper[AgentToolContext]):
    await write_file_impl(ctx, "/a", "1")
    await write_file_impl(ctx, "/b", "2")
    assert (await list_files_impl(ctx)).splitlines() == ["a", "b"]


async def test_list_files_emits_shell_usable_relative_paths(
    ctx: RunContextWrapper[AgentToolContext],
):
    """`list_files` is the agent's ONLY source of truth for "what files exist",
    so the strings it prints must be the ones that work EVERYWHERE. The internal
    key is `/a.txt`, but in the shell `/` is the system root, not the workspace —
    an agent that copies `/a.txt` into `exec` or a python script misses the file.
    So the listing speaks the one dialect valid in both: relative."""
    await write_file_impl(ctx, "/a.txt", "1")
    await write_file_impl(ctx, "data/x.csv", "2")

    listed = (await list_files_impl(ctx)).splitlines()
    assert listed == ["data/", "a.txt"]  # one level, directories first
    assert not any(p.startswith("/") for p in listed)

    # …and every string it emits still round-trips through the file tools, which
    # stay permissive about the form (`/a.txt` / `./a.txt` / `a.txt` all work) —
    # including the sub-directory, which is what the agent passes back in.
    assert await exists_impl(ctx, "a.txt") is True
    assert await read_file_impl(ctx, "a.txt") == "1"
    assert (await list_files_impl(ctx, "data/")).splitlines() == ["data/x.csv"]


async def test_list_files_prefix_filter_accepts_either_form(
    ctx: RunContextWrapper[AgentToolContext],
):
    """The prefix argument stays permissive on input — a leading slash is still
    accepted — while the output is relative either way."""
    await write_file_impl(ctx, "data/x.csv", "1")
    await write_file_impl(ctx, "other.txt", "2")
    assert (await list_files_impl(ctx, "/data")).splitlines() == ["data/x.csv"]
    assert (await list_files_impl(ctx, "data")).splitlines() == ["data/x.csv"]


async def test_exists_returns_bool(ctx: RunContextWrapper[AgentToolContext]):
    await write_file_impl(ctx, "/x", "x")
    assert await exists_impl(ctx, "/x") is True
    assert await exists_impl(ctx, "/missing") is False


async def test_delete_removes_file(ctx: RunContextWrapper[AgentToolContext]):
    await write_file_impl(ctx, "/x", "x")
    await delete_file_impl(ctx, "/x")
    assert await exists_impl(ctx, "/x") is False


async def test_delete_missing_returns_error_string(
    ctx: RunContextWrapper[AgentToolContext],
):
    out = await delete_file_impl(ctx, "/never")
    assert "error" in out.lower()


async def test_exec_after_write_file_sees_the_content(
    ctx: RunContextWrapper[AgentToolContext],
):
    """The user-facing promise: agent writes via write_file (which lands
    in FileStore), then runs a shell command that needs to read the file.
    The flush hook is what makes this work."""
    await write_file_impl(ctx, "/notes.txt", "hello from agent")
    out = await exec_impl(ctx, ["cat", "/notes.txt"])
    assert "hello from agent" in out


def test_build_tools_returns_the_workspace_set_by_default():
    tools = build_tools()
    names = {t.name for t in tools}
    # file/exec tools + the two sub-agent bridges (`ask_knowledge_base` for
    # KB lookups, `infer_modules` for process-module classification) +
    # `request_wiki_update` (#397); NOT `kb_search`, which is the sub-agents'
    # OWN opt-in tool.
    assert names == {
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
        "update_todos",  # #613
        "show_file",
    }
    assert "kb_search" not in names
    assert all(isinstance(t, FunctionTool) for t in tools)


def test_show_file_needs_no_opting_in():
    """`show_file` is part of the DEFAULT workspace set, not an opt-in extra.

    Every bundled workspace preset stores `allowed_tools: null` and resolves that
    default set, so a tool left out of it never reaches them no matter what the
    app.json ceiling says (#613's live-probe regression). Being able to put a file
    in front of the user is not a per-conversation nicety — an ability the user has
    to remember to enable is, in practice, an ability they do not have. This is
    also why it is a tool and not a skill: skills are per-message opt-in.
    """
    assert "show_file" in {t.name for t in build_tools()}


def test_build_tools_filters_by_allowed_list():
    tools = build_tools(allowed=["exec", "read_file"])
    assert {t.name for t in tools} == {"exec", "read_file"}


def test_build_tools_normalizes_legacy_ls_name():
    """A stored allowed_tools list written before the ls→list_files rename (#241)
    still provisions the tool — the legacy name is normalised to the current
    name, not silently dropped. The old name is NOT a callable alias."""
    names = {t.name for t in build_tools(allowed=["ls", "read_file"])}
    assert names == {"list_files", "read_file"}


def test_build_tools_registers_a_repeated_name_once():
    """A name listed twice in `allowed_tools` must still produce ONE tool. Two
    definitions sharing a name is not a cosmetic wart — the provider rejects the
    whole tool payload, so a single duplicated line in a hand-written config
    takes down every turn that config drives."""
    names = [t.name for t in build_tools(allowed=["read_file", "read_file"])]
    assert names.count("read_file") == 1


def test_build_tools_collapses_a_legacy_alias_onto_its_current_name():
    """`ls` normalises to `list_files` (#241), so a stored list carrying BOTH the
    old and the new name collides after the rename — the same duplicate, arrived
    at by a route nobody writes on purpose."""
    names = [t.name for t in build_tools(allowed=["ls", "list_files"])]
    assert names.count("list_files") == 1


def test_kb_search_logs_underlying_exception_before_reraising(caplog):
    """When the retriever errors (e.g. Ollama down, LiteLLM HTTP failure),
    `kb_search_impl` previously let the exception go straight to the
    agents-SDK tool-error wrapper without ever hitting the server log.
    The operator saw a silent run; the LLM saw "An error occurred…"
    and tended to synthesize a polite refusal.

    Fix: log the exception with traceback before re-raising, so the
    server-side `uvicorn` log shows the actual root cause."""
    import logging

    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, kb_search_impl

    class _BrokenRetriever:
        def search(self, query, collection_ids, on_progress, *, enhancements=None, **kw):
            raise ConnectionRefusedError("Ollama at http://localhost:11434 not reachable")

    ctx = RunContextWrapper(AgentToolContext(retriever=_BrokenRetriever()))  # ty: ignore
    with caplog.at_level(logging.ERROR):
        try:
            kb_search_impl(ctx, "voids")
        except ConnectionRefusedError:
            pass  # re-raised so the SDK still wraps it for the agent
        else:
            raise AssertionError("kb_search should re-raise the underlying error")
    # The server log got the message AND the traceback — operator can
    # see the failing tool name and the underlying exception together.
    matching = [r for r in caplog.records if "kb_search" in r.message.lower()]
    assert matching, f"expected a kb_search log record, got {caplog.records}"
    rec = matching[0]
    assert rec.exc_info is not None, "log record should carry the exception info"
    exc_text = str(rec.exc_info[1]).lower()
    assert "ollama" in exc_text  # underlying cause is named


class _RecordingRetriever:
    """Captures the `enhancements` it was called with, returns no passages."""

    def __init__(self):
        self.enhancements = "UNSET"

    # **kw: this double asserts the ENHANCEMENT cascade; the query's scope kwargs are
    # not its subject, so it shouldn't break every time retrieval gains a filter.
    def search(self, query, collection_ids, on_progress, *, enhancements=None, **kw):
        self.enhancements = enhancements
        return []


def test_kb_search_caller_depth_overrides_llm_tool_args():
    """#68: when the KB-chat user picks a depth (caller context sets
    expand/hyde/rerank explicitly), the model's OWN kb_search args must
    not override it. 'quick' (0/0/False) stays quick even when the model
    asks for expand=5, hyde=5, rerank=True."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, kb_search_impl
    from workspace_app.kb.retriever import Enhancements

    retr = _RecordingRetriever()
    ctx = RunContextWrapper(
        AgentToolContext(
            retriever=retr,  # ty: ignore[invalid-argument-type]
            kb_enhancements=Enhancements(expand=0, hyde=0, rerank=False),
        )
    )
    kb_search_impl(ctx, "voids", expand=5, hyde=5, rerank=True)
    assert retr.enhancements == Enhancements(expand=0, hyde=0, rerank=False)


def test_kb_search_uses_llm_args_when_caller_leaves_depth_unset():
    """#68 other half: 'standard' mode sends no depth payload (caller is
    None), so the model's own kb_search args still tune the search."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, kb_search_impl
    from workspace_app.kb.retriever import Enhancements

    retr = _RecordingRetriever()
    ctx = RunContextWrapper(AgentToolContext(retriever=retr))  # no kb_enhancements  # ty: ignore
    kb_search_impl(ctx, "voids", expand=2, hyde=1, rerank=True)
    assert retr.enhancements == Enhancements(expand=2, hyde=1, rerank=True)


def test_kb_search_cascade_is_per_knob():
    """#68: the override is per-knob. A caller that pins only `expand`
    (others left None) keeps that knob authoritative while the model's
    args still fill the knobs the caller didn't set."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, kb_search_impl
    from workspace_app.kb.retriever import Enhancements

    retr = _RecordingRetriever()
    ctx = RunContextWrapper(
        AgentToolContext(retriever=retr, kb_enhancements=Enhancements(expand=0))  # ty: ignore
    )
    kb_search_impl(ctx, "voids", expand=9, hyde=3, rerank=True)
    assert retr.enhancements == Enhancements(expand=0, hyde=3, rerank=True)


async def test_ask_knowledge_base_delegates_to_the_context_bridge():
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl
    from workspace_app.resources.conversation import Citation

    received: dict[str, object] = {}

    cite = Citation(
        marker=1,
        collection_id="c",
        document_id="d",
        filename="f.md",
        start=0,
        end=10,
        source_chunk_ids=["ck"],
    )

    async def fake_run(
        purpose: str,
        payload: str,
        emit: object,
        origin_id: object,
        collection_ids: object = None,
        withheld_sink: object = None,
    ) -> tuple[str, list[Citation]]:
        # The bridge returns BOTH the synthesized answer AND its resolved
        # citations so the impl can stash them on ctx for the turn engine
        # to attach to the persisted RCA tool message.
        assert purpose == "kb_chat"
        received["emit"] = emit
        received["origin_id"] = origin_id
        # No tiers configured ⇒ the impl passes None (search the whole KB).
        received["collection_ids"] = collection_ids
        return f"KB answer to: {payload}", [cite]

    def sink(b: bytes) -> None: ...

    actx = AgentToolContext(run_subagent=fake_run, on_exec_output=sink)
    ctx = RunContextWrapper(actx)
    out = await ask_knowledge_base_impl(ctx, "why did zone three drift?")
    # The tool itself still returns just the answer text (what the LLM sees) —
    # citations are persisted via ctx, not echoed in the function tool's
    # output (which would burn tokens + show as raw JSON in the tool card).
    assert out == "KB answer to: why did zone three drift?"
    # With no priority tiers, the impl searches the whole KB (today's behaviour)
    # and adds no tier banner.
    assert received["collection_ids"] is None
    # The run's output sink is handed to the bridge so KB progress can stream.
    assert received["emit"] is sink
    # Citations are stashed in CALL ORDER on the context, bucketed by
    # tool name; the turn engine pairs them with each successive
    # ask_knowledge_base tool message.
    assert actx.subagent_citations["ask_knowledge_base"] == [[cite]]


async def test_ask_knowledge_base_two_calls_stash_per_call_citations():
    """Two ask_knowledge_base calls in one turn stash TWO citation lists, in
    order, so the persist step can pair them with each tool message."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl
    from workspace_app.resources.conversation import Citation

    def _cite(n: int) -> Citation:
        return Citation(
            marker=n,
            collection_id="c",
            document_id=f"d{n}",
            filename=f"f{n}.md",
            start=0,
            end=10,
            source_chunk_ids=[f"ck{n}"],
        )

    calls: list[str] = []

    async def fake_run(purpose, payload, emit, origin_id, collection_ids=None, withheld_sink=None):
        assert purpose == "kb_chat"
        calls.append(payload)
        return f"a:{payload}", [_cite(len(calls))]

    actx = AgentToolContext(run_subagent=fake_run)
    ctx = RunContextWrapper(actx)
    await ask_knowledge_base_impl(ctx, "q1")
    await ask_knowledge_base_impl(ctx, "q2")
    assert [cs[0].marker for cs in actx.subagent_citations["ask_knowledge_base"]] == [1, 2]


async def test_ask_knowledge_base_scopes_the_subagent_to_the_requested_rank():
    """#280: with priority tiers configured, rank picks the tier's collection
    subset (exclusive — rank 1 searches ONLY tier 1) and the answer carries a
    banner telling the agent how to widen."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl

    seen: dict[str, object] = {}

    async def fake_run(purpose, payload, emit, origin_id, collection_ids=None, withheld_sink=None):
        seen["scope"] = collection_ids
        return "ans", []

    actx = AgentToolContext(run_subagent=fake_run, collection_tiers=[["a", "b"], ["c"], ["d"]])
    ctx = RunContextWrapper(actx)

    out0 = await ask_knowledge_base_impl(ctx, "q", rank=0)
    assert seen["scope"] == ["a", "b"]  # rank 0 = top-priority tier only
    assert "rank=1" in out0 and "tier 0 of 3" in out0  # widen hint

    out1 = await ask_knowledge_base_impl(ctx, "q", rank=1)
    assert seen["scope"] == ["c"]  # exclusive: rank 1 is ONLY tier 1
    assert "rank=2" in out1


async def test_ask_knowledge_base_last_tier_says_no_more_tiers():
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl

    async def fake_run(purpose, payload, emit, origin_id, collection_ids=None, withheld_sink=None):
        return "ans", []

    actx = AgentToolContext(run_subagent=fake_run, collection_tiers=[["a"], ["b"]])
    out = await ask_knowledge_base_impl(RunContextWrapper(actx), "q", rank=1)
    assert "no more tiers" in out.lower()
    assert "rank=2" not in out  # nothing to widen to


async def test_ask_knowledge_base_rank_past_last_tier_stops_without_searching():
    """A rank beyond the lowest tier doesn't run the sub-agent — it tells the
    agent to stop. The citation pool still gets an entry so the persist step's
    Nth-message↔Nth-bucket pairing stays aligned."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl

    ran = {"n": 0}

    async def fake_run(purpose, payload, emit, origin_id, collection_ids=None, withheld_sink=None):
        ran["n"] += 1
        return "ans", []

    actx = AgentToolContext(run_subagent=fake_run, collection_tiers=[["a"], ["b"]])
    out = await ask_knowledge_base_impl(RunContextWrapper(actx), "q", rank=2)
    assert ran["n"] == 0  # never searched
    assert "no priority tier 2" in out.lower() or "no priority tier" in out.lower()
    assert actx.subagent_citations["ask_knowledge_base"] == [[]]  # alignment preserved


async def test_mention_user_delegates_to_the_context_hook():
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, mention_user_impl

    calls: list[tuple[str, list[str], str]] = []

    def fake_mention(investigation_id: str, user_ids: list[str], note: str) -> None:
        calls.append((investigation_id, user_ids, note))

    ctx = RunContextWrapper(AgentToolContext(investigation_id="inv-1", mention=fake_mention))
    out = await mention_user_impl(ctx, "alice", "please review the SPC")
    assert "alice" in out
    assert calls == [("inv-1", ["alice"], "please review the SPC")]


async def test_lookup_user_resolves_a_handle_to_name_id_section_and_email():
    """#275 — the agent only sees `[Name (handle)]:`, never the canonical id.
    lookup_user resolves the handle it CAN see back to the full record,
    surfacing the id so it can feed `mention_user` (id != handle here proves
    the bridge isn't relying on id==handle)."""
    from workspace_app.agent import AgentToolContext, lookup_user_impl
    from workspace_app.users import MockUserDirectory, User

    users = MockUserDirectory(
        [User(id="e123", name="Alice Chen", section="Reflow", email="alice.chen@acme.test")]
    )
    ctx = RunContextWrapper(AgentToolContext(users=users))
    out = await lookup_user_impl(ctx, "alice.chen")
    assert (
        out == "Alice Chen — handle alice.chen, id e123, section Reflow, email alice.chen@acme.test"
    )


async def test_lookup_user_omits_section_and_email_when_the_record_lacks_them():
    from workspace_app.agent import AgentToolContext, lookup_user_impl
    from workspace_app.users import MockUserDirectory, User

    # No email → the handle falls back to the id; no section either. The line
    # still carries name + handle + id and simply drops the empty fields.
    users = MockUserDirectory([User(id="contractor-1", name="Dana")])
    ctx = RunContextWrapper(AgentToolContext(users=users))
    out = await lookup_user_impl(ctx, "contractor-1")
    assert out == "Dana — handle contractor-1, id contractor-1"


async def test_lookup_user_returns_a_graceful_note_for_an_unknown_handle():
    from workspace_app.agent import AgentToolContext, lookup_user_impl
    from workspace_app.users import MockUserDirectory, User

    users = MockUserDirectory([User(id="e123", name="Alice Chen", email="alice.chen@acme.test")])
    ctx = RunContextWrapper(AgentToolContext(users=users))
    out = await lookup_user_impl(ctx, "ghost")
    assert "ghost" in out
    assert "id " not in out  # no record → no canonical id leaked


async def test_lookup_user_reports_unavailable_when_no_directory_is_wired():
    from workspace_app.agent import AgentToolContext, lookup_user_impl

    ctx = RunContextWrapper(AgentToolContext())  # users left None (e.g. a non-shared turn)
    out = await lookup_user_impl(ctx, "alice.chen")
    assert out.startswith("error:")


async def test_exec_output_is_capped_by_the_context_budget(
    ctx: RunContextWrapper[AgentToolContext],
):
    """#44 — the exec tool truncates oversized output using the context's
    exec_output_max_chars, so a `grep`-style flood can't blow up the
    model's context. (echo of a long arg stands in for the big output.)"""
    ctx.context.exec_output_max_chars = 500
    flood = "A" * 5000 + " TAILMARK"
    out = await exec_impl(ctx, ["echo", flood])
    assert len(out) < 1000
    assert "omitted" in out
    assert "exit_code=0" in out


@pytest.mark.integration
async def test_listed_path_works_verbatim_in_a_real_shell(tmp_path):
    """THE defect, end to end, against a REAL shell (the mock sandbox stores
    paths verbatim in a dict, so only a real process can prove this): whatever
    `list_files` prints must be runnable as-is via `exec`. With the old
    `/`-prefixed listing the agent copied `/notes.txt` into a command and hit
    the system root, so `cat` failed — and no amount of prompt prose beat what
    the tool had just shown it."""
    from workspace_app.sandbox.local_process import LocalProcessSandbox
    from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec

    sandbox = LocalProcessSandbox(root_dir=tmp_path, isolate=False)
    holder: dict[str, SandboxHandle] = {}

    async def _resolve(ws: str) -> SandboxHandle | None:
        return holder.get(ws)

    files = WorkspaceFiles(MemoryFileStore(), sandbox, _resolve)

    async def wake(on_progress=None, tools=None) -> SandboxHandle:
        h = await sandbox.create(SandboxSpec(tools=tools))
        holder["inv-1"] = h
        return h

    ctx = RunContextWrapper(
        AgentToolContext(
            investigation_id="inv-1", sandbox=sandbox, files=files, ensure_sandbox_via=wake
        )
    )
    await ctx.context.ensure_sandbox()
    await write_file_impl(ctx, "notes.txt", "hello")

    [listed] = (await list_files_impl(ctx)).splitlines()
    out = await exec_impl(ctx, ["cat", listed])  # the EXACT string the agent was shown
    assert "exit_code=0" in out
    assert "hello" in out


def _tool_named(name: str):
    for t in build_tools([name]):
        if t.name == name:
            return t
    raise AssertionError(f"{name} not built")


def test_a_tool_with_optional_args_states_what_absent_looks_like():
    """Nothing ever told the model how to say "I don't want to set this".

    Strict mode lists EVERY property in `required` and strips the `null` default,
    so a tool like `kb_search` (one required arg, seven optional) presents seven
    slots the model must fill on its very first call, with no stated convention
    for leaving one empty. It has to invent one — and a model that thinks in
    Python invents `None`, which is not JSON. The first call then fails, and the
    validation error becomes the only spec it ever receives; that is why the
    second or third attempt works.

    This is NOT the case of prose contradicting the schema — the schema says
    nothing on the subject. It is a sentence the interface was missing."""
    desc = _tool_named("kb_search").description or ""
    assert "null" in desc.lower()
    # and it names the actual optional args, so the convention is not abstract
    assert "page_from" in desc


def test_the_note_is_generic_not_written_for_one_tool():
    """The same gap exists for every tool with optional args, so the sentence is
    derived from the schema rather than hand-written into one docstring — a tool
    that grows an optional arg tomorrow is covered without anyone remembering."""
    desc = _tool_named("make_deck").description or ""
    assert "null" in desc.lower()
    assert "out_path" in desc


def test_a_tool_with_no_optional_args_is_left_alone():
    """No optional args ⇒ nothing to leave unset ⇒ no note. The addition must not
    become boilerplate stapled to every tool."""
    desc = _tool_named("link_entity").description or ""
    assert "null" not in desc.lower()


async def test_tool_confirmations_never_hand_a_rooted_path_back(
    ctx: RunContextWrapper[AgentToolContext],
):
    """A tool's own confirmation is the agent's second-strongest evidence about
    what a path here looks like, after `list_files` — and it used to echo the
    argument verbatim. So a model that guessed `/notes.md` got that guess
    CONFIRMED by a successful write ("wrote 2 bytes to /notes.md"), carried it to
    `exec`, and hit the system root. Input stays permissive (#549); what we hand
    BACK is the one form that works everywhere."""
    assert await write_file_impl(ctx, "/notes.md", "hi") == "wrote 2 bytes to notes.md"
    assert await edit_file_impl(ctx, "/notes.md", "hi", "yo") == "edited notes.md"

    # the rejection paths name the file too — a retry is built from this text
    clash = await write_file_impl(ctx, "/notes.md", "again")
    assert clash.startswith("error: notes.md already exists")
    stale = await edit_file_impl(ctx, "/notes.md", "nothing like this", "x")
    assert stale.startswith("error: could not apply the edit to notes.md")

    assert await delete_file_impl(ctx, "/notes.md") == "deleted notes.md"
    assert await read_file_impl(ctx, "/gone.md") == "error: file not found: gone.md"
    assert await delete_file_impl(ctx, "/gone.md") == "error: file not found: gone.md"


async def test_tool_confirmations_leave_a_relative_path_unchanged(
    ctx: RunContextWrapper[AgentToolContext],
):
    """The same messages when the agent already speaks the right dialect — the
    normalisation is idempotent, so nothing gains or loses a `./`."""
    assert await write_file_impl(ctx, "data/x.csv", "hi") == "wrote 2 bytes to data/x.csv"
    assert await edit_file_impl(ctx, "data/x.csv", "hi", "yo") == "edited data/x.csv"
    assert await delete_file_impl(ctx, "data/x.csv") == "deleted data/x.csv"


async def test_ask_knowledge_base_books_its_slot_before_anything_that_can_raise():
    """Its sub-agent call can RAISE — an LLM error, a timeout, a transport
    failure — and the SDK turns that into a tool message just the same, so the
    exit consumes a pairing slot. The `assert run is not None` guard sat ABOVE
    the booking, which is one of the four exits the helper's own docstring
    enumerates; both are below it now."""
    from agents import RunContextWrapper

    from workspace_app.agent import AgentToolContext, ask_knowledge_base_impl

    # (a) the wiring assert — nothing is wired at all
    unwired = AgentToolContext()
    with pytest.raises(AssertionError):
        await ask_knowledge_base_impl(RunContextWrapper(unwired), "q")
    assert unwired.subagent_citations["ask_knowledge_base"] == [[]]

    # (b) the sub-agent itself falling over
    async def _boom(*_a, **_k):
        raise RuntimeError("the sub-agent fell over")

    ctx = AgentToolContext(run_subagent=_boom)
    with pytest.raises(RuntimeError):
        await ask_knowledge_base_impl(RunContextWrapper(ctx), "q")
    assert ctx.subagent_citations["ask_knowledge_base"] == [[]]

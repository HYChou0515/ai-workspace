"""Run one package tool for an item, outside a turn (#WUI P4).

A WUI has no network of its own — its CSP forbids it — so reaching an external
system means asking the platform to run a **tool**, and this is where that
request lands. The point is not convenience: it is that credentials stay on the
platform (a tool reads them from the item's environment, #750) and never enter
a browser, and that a page cannot decide which credentials to ask a person for.

**What may be called is the app's decision, not the page's.** The ceiling is
``app.json``'s ``tools[]`` narrowed by the profile and the item's own toggles —
resolved through the SAME ``AppCatalog.resolve`` a turn uses, so a WUI can never
reach a tool the agent in that item could not. The page's own ``tools:``
declaration is disclosure rather than security: it is enforced in the bridge, so
a page cannot quietly use something it did not announce, but it can only ever
narrow what this route already allows.

Only **package** tools are reachable. A built-in is shaped for a model — it
truncates, it appends "[truncated: …]" to its own output, it returns errors as
prose — and a program that reads that gets JSON with a hole in it, silently. A
page needing a platform capability gets an HTTP route, not a built-in tool.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..agent.context import AgentToolContext
from ..apps.catalog import finalize_tool_grants
from ..apps.subagents import SubagentDef
from ..sandbox.protocol import ExecResult, Sandbox, SandboxSpec
from ..tooling.external import ExternalTools
from ..tooling.registry import (
    THIRD_PARTY_SEP,
    CommandInfo,
    PackageInfo,
    exec_package_command,
    find_allowed_command,
)
from ..workflow.offered import no_such_workflow, wont_parse
from ..workflow.orchestrator import TooManyPageJobs
from ..workflow.run import PageJob
from .env_layers import PersonEnv, resolve, resolve_env
from .locator import ItemLocator
from .private_env import PrivateEnvStore, private_layer
from .request_env import IRequestEnv
from .timeutil import now_ms
from .turn_context import resolve_item_tools

logger = logging.getLogger(__name__)

# What a backend returns when it killed the command on its own deadline
# (`ExecResult.exit_code`'s documented convention).
EXIT_TIMED_OUT = 124
# Ours, for a stream that ended without the command reporting. The page needs a
# non-zero code to show a failure, and inventing `1` would read as "the build
# failed" when what failed was reaching it.
EXIT_STREAM_BROKE = -1


class CallToolBody(BaseModel):
    args: dict[str, Any] = {}


class BuildBody(BaseModel):
    """Which page to rebuild. The folder, not a command: `package.json`'s
    `scripts.build` decides what a build IS, which is the standard place for
    that and keeps a page — LLM-written — from choosing what a human's click
    executes."""

    folder: str


def _build_dir(folder: str) -> str | None:
    """The folder as a path RELATIVE to the workspace root, or `None`.

    Relative because that is what the shell can use: `exec` runs with the
    workspace root as its working directory (`Sandbox.exec`'s contract), so the
    workspace path `/page` names the filesystem root instead — outside the
    workspace entirely, and inside the userns jail it names the infra area
    beside it, which EXISTS. Passing the workspace path through made every build
    die with `sh: cd: can't cd to /built` before running anything; it took
    opening a real page to see, because the string is present either way.

    Checked on normalised SEGMENTS and interpolated into a shell command only
    after passing: this string reaches `sh -c`, so "looks fine" is not the bar.
    The workspace root itself is refused — a build there would run over the
    whole item, and no page lives there anyway (a root-level view file cannot
    write, so it cannot be a built page)."""
    if not folder.startswith("/"):
        return None
    out: list[str] = []
    for seg in folder.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            return None  # not "pop": a build path is not a place to be clever
        out.append(seg)
    return "/".join(out) or None


# What a rebuild runs, once the shell is in the page's folder.
#
# The install is not optional politeness: `node_modules/` is in the mirror's
# ignore list — derived, and huge — so a recycled sandbox comes back with the
# source, `package.json` and the lockfile, and no dependencies. A build that
# assumed them fails on the first click after a recycle, and the remedy would be
# "get someone to run pnpm install", which is the friction a WUI exists to
# remove. With `node_modules` already in place and the lock unchanged this costs
# about a second, so it is not worth making conditional.
#
# `--frozen-lockfile` where there IS a lock: two installs from one lock that
# resolve differently make the lock pointless. Where there is none — a page
# installing for the first time — the plain install is what WRITES it, and
# `--frozen-lockfile` would refuse instead.
#: What the log says between the two halves.
#:
#: One command, two steps, and before this ONE verdict for both: a failed
#: install reads as "Build failed (exit 1)" and you have to know that
#: `&&` means the build never ran at all. Worse, the natural place to look
#: instead — `node_modules` in the file tree — cannot answer it either: a page
#: with no dependencies installs successfully and leaves a single metadata file,
#: which is indistinguishable from an install that died on its first write.
#:
#: So the log says so itself. Printed by the shell between the halves, it costs
#: nothing, every page gets it without its author remembering anything, and the
#: line is there whether the build then succeeds or fails.
BUILD_STEP_MARK = "--- dependencies ready, running the build ---"

_BUILD = (
    "if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install; fi"
    f" && echo {shlex.quote(BUILD_STEP_MARK)}"
    " && pnpm run build"
)


class AgentCallBody(BaseModel):
    """What a page asks a sub-agent (#942 D4): text in."""

    input: str = ""


class AgentCallOut(BaseModel):
    """What comes back (#942 D7): the sub-agent's answer, and whether that
    answer is a failure — `run_agent`'s own sentence when it is, so a page
    can show it as it stands."""

    output: str
    failed: bool


class JobBody(BaseModel):
    """`startTool` / `startAgent` (#942 D8): which capability, and its input —
    the tool's args object, or the sub-agent's text."""

    kind: Literal["tool", "agent"]
    name: str
    input: Any = None


class JobStarted(BaseModel):
    job_id: str


class JobOut(BaseModel):
    """`job.status()`. ``output`` is what the matching `call…` returns, once
    the job is done; ``error`` says why it failed."""

    state: Literal["running", "done", "failed", "stopped"]
    output: dict[str, Any] | None = None
    error: str | None = None


#: How long `callAgent` waits for its answer before stopping the job (#942 §3.1)
#: — the same minute a tool call gets. Module-level so a test can shorten it.
CALL_TIMEOUT_S = 60.0
# The turn engine's own cancel watcher polls at this pace too.
_POLL_S = 0.5

# A page job's work, given the conversation it was opened on.
_Work = Callable[[Any], Awaitable[dict[str, Any]]]


class RunBody(BaseModel):
    """Which workflow to start, and what to hand it.

    ``payload`` is opaque — the platform passes it through untouched. Everything
    domain-shaped lives in there, which is what keeps the platform from learning
    what any of this means.
    """

    workflow: str
    # `with` on the wire, because that is how it reads to a page author and it
    # is the word `schedules.json` already uses — one vocabulary for "start this
    # workflow with this", whether now or on a clock. `payload` in Python
    # because `with` is a keyword.
    payload: dict[str, Any] = Field(default_factory=dict, alias="with")


class CallToolOut(BaseModel):
    """What the page gets back.

    The tool's stdout is handed over verbatim: the caller is a program, so
    anything we appended to explain a truncation would arrive as part of its
    data. `exit_code` carries the tool's own contract unchanged."""

    output: str
    exit_code: int


async def _no_workflows(_item: str) -> Sequence[str]:
    """The default when nothing is wired: an item offers no workflow to its pages."""
    return ()


async def _no_problem(_item: str, _workflow_id: str) -> str | None:
    """The default when nothing is wired: every offered workflow is taken to parse."""
    return None


def register_wui_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    sandbox: Sandbox,
    registry: Any,
    packages: list[PackageInfo] | None,
    prebuilt_dir: Path | None,
    resolve_external: Callable[[str], Any] | None = None,
    view_plugin_artifacts: Mapping[str, str],
    request_env: IRequestEnv | None = None,
    private_env: PrivateEnvStore | None = None,
    get_user_id: Callable[[], str] | None = None,
    orchestrator: Any = None,
    turn_engine: Any = None,
    workflows_for: Callable[[str], Awaitable[Sequence[str]]] = _no_workflows,
    workflow_problem_for: Callable[[str, str], Awaitable[str | None]] = _no_problem,
    subagents_for: Callable[[str], Awaitable[Sequence[SubagentDef]]] | None = None,
    drive_agent: Callable[..., Awaitable[tuple[str, bool]]] | None = None,
) -> None:
    """Mount the WUI tool-call route.

    ``resolve_external`` is a seam for tests; it defaults to the same host
    round-trip a turn makes.

    ``request_env`` is the SAME seam a chat send composes (#714). What it
    carries is the caller's own credential — a cookie their gateway exchanged
    for a token — so without it a tool that answers in the chat 401s from the
    page written to call it.

    It reaches ``wui_call_tool`` and DELIBERATELY NOT ``wui_build``. A tool call
    answers one person and dies with the exec; a build writes ``dist/``, which
    is durable and is served to every viewer of the item. The two run as
    different identities on purpose — see the note at the composition itself.
    """

    # NOT "first party": in this codebase that names the bundled `sample-tools`
    # packages, which ARE reachable — the unreachable set is the agent's
    # BUILT-INS, and inverting the vocabulary here is how a future reader talks
    # themselves into the opposite rule.
    bundled: Sequence[PackageInfo] = packages or []

    async def _request_env(request: Request, item_id: str) -> PersonEnv:
        """What the request behind this click contributes to the tool env.

        Empty when the deploy named no impl — the seam ships unimplemented, and
        every deploy that has not opted in must behave exactly as before.

        Only ``wui_call_tool`` calls this; a build takes the item's variables
        alone (see the note there).

        A failing impl REFUSES, and does so before anything runs. Carrying on
        with `{}` would run the tool as nobody in particular, answering from
        whatever identity the item's own variables happen to describe — which
        looks like success. An impl that would rather degrade catches its own
        errors and returns `{}`; only it knows whether running without the value
        means anything. Its message is not relayed: only the impl knows whether
        it built that string out of the cookie it was reading.
        """
        uid = get_user_id() if get_user_id is not None else ""
        try:
            # None = no seam configured (the person's own values still apply).
            fresh = (
                None
                if request_env is None
                else await request_env.env_for(request, user_id=uid, item_id=item_id)
            )
        except Exception:
            logger.exception("wui: request env source failed for item %s", item_id)
            raise HTTPException(
                status_code=500,
                # A SENTENCE, not `{"error": ...}`. The chat client maps that
                # code into human words; the WUI clients keep `detail` only when
                # it is a string, so a dict reached the page as "could not be run
                # (500)" — on every open, with nothing to act on. The impl's OWN
                # message is still not relayed: only the impl knows whether it
                # built that string out of the cookie it was reading.
                detail="This page could not confirm who you are. Sign in again, then reopen it.",
            ) from None
        return await private_layer(private_env, user_id=uid, item_id=item_id, fresh=fresh)

    async def _external(item_id: str) -> ExternalTools:
        if resolve_external is not None:
            return await resolve_external(item_id)
        # With the view plugins: this call may create the item's sandbox.
        return await resolve_item_tools(
            sandbox, locator, item_id, plugin_artifacts=view_plugin_artifacts
        )

    @app.post("/a/{slug}/items/{item_id}/wui/build")
    # No `request` parameter: this route composes no per-request environment,
    # and a `Request` in the signature would advertise that it does.
    async def wui_build(slug: str, item_id: str, body: BuildBody) -> StreamingResponse:
        """Rebuild a page, streaming the build's own output as it arrives.

        A page written with a bundler has two halves — the source someone edits
        and the `dist/` a viewer sees — and they go out of step the moment a
        rebuild is forgotten: the page renders, unchanged, with nothing saying
        why. This is how that stops being possible: the person looking at the
        page can rebuild it themselves.

        **Streaming is the feature, not a detail.** A build takes tens of
        seconds and fails often while someone is iterating, and its compiler
        errors are the whole value. A route that answered only at the end would
        give a spinner and no idea whether anything was happening.

        The verb is `execute`: this runs a command in the item's sandbox, the
        same thing a notebook cell does.
        """
        investigation_id = locator.require_access(slug, item_id, "execute")
        cwd = _build_dir(body.folder)
        if cwd is None:
            raise HTTPException(status_code=400, detail=f"{body.folder} is not a page folder.")

        # The item's variables ONLY — deliberately not the request's.
        #
        # A build writes `dist/`, which is mirrored to durable storage and
        # inlined into the document served to EVERY viewer of this item. A
        # bundler's whole job is to put its environment into that artifact:
        # Vite's `loadEnv` picks `VITE_`-prefixed names out of `process.env`
        # and `define`s them into the bundle. So a per-request credential
        # reaching here would be baked into a file other people download —
        # one person's credential in everybody's copy. The same holds for a
        # person's stored PRIVATE values (`plan-wui-viewer-login`): the build
        # runs as NOBODY, so its private layer is empty.
        #
        # It still follows the item's policy, through the same `resolve_env`
        # every other path uses (D11, user 2026-10-07): a `private_only` name
        # reaches a tool only as the person it runs for, so a build — which runs
        # for nobody — gets none, even when the shared layer holds one.
        #
        # A tool call is the opposite shape: its output goes to the one person
        # who asked, and dies with the exec. That is why `wui_call_tool` DOES
        # compose the request's env and this does not. They are not the same
        # question wearing different hats — one produces a shared artifact and
        # the other does not, so they run as different identities on purpose.
        #
        # A registry credential a build genuinely needs belongs on the item,
        # where everyone the page is shared with is already entitled to what it
        # builds.
        layers = locator.env_layers_of(investigation_id)
        env = resolve_env(shared=layers.shared, private={}, policy=layers.policy)

        session = await registry.session(investigation_id)
        handle = await registry.ensure_handle(session)
        # `exec` takes no working directory, so the shell provides one — relative
        # to the workspace root it already starts in. `./` and `--` so a folder
        # named `-p` is a folder and not an option, and `shlex.quote` because
        # `_build_dir` decided this is a workspace path, not that it is safe to
        # paste into a shell.
        script = f"cd -- {shlex.quote(f'./{cwd}')} && {_BUILD}"

        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_output(chunk: bytes) -> None:
            # Called from whatever thread the backend reads on; hop to the loop
            # rather than touching the queue directly.
            loop.call_soon_threadsafe(queue.put_nowait, chunk)

        async def run() -> ExecResult:
            try:
                return await sandbox.exec(
                    handle, ["sh", "-c", script], on_output=on_output, env=env
                )
            finally:
                # The SAME hop the chunks take, so the order is right by
                # construction rather than by luck. `call_soon_threadsafe` from
                # this thread DEFERS, so a straight `put` here overtook output
                # a backend had already handed us — the build's log arrived
                # after the line saying it had finished, or not at all.
                loop.call_soon_threadsafe(queue.put_nowait, None)

        def frame(payload: dict[str, Any]) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        async def gen() -> AsyncIterator[str]:
            task = asyncio.ensure_future(run())
            # A chunk is a byte FRAGMENT, not a string — the backend reads a
            # fixed block at a time, so a multi-byte character lands astride the
            # boundary. Decoding each chunk on its own turned vite's `✓`, which
            # it prints on every build, into a replacement character.
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            try:
                while (chunk := await queue.get()) is not None:
                    if text := decoder.decode(chunk):
                        yield frame({"type": "output", "text": text})
                if tail := decoder.decode(b"", final=True):
                    yield frame({"type": "output", "text": tail})

                result = await task
                if result.exit_code == EXIT_TIMED_OUT:
                    # The backend explains itself in `stderr`, appended AFTER the
                    # output pumps have stopped — so it never reaches `on_output`
                    # and never reaches the page. Without this the reader watches
                    # the log stop mid-install and gets a bare number.
                    #
                    # Both deadlines return 124 and the code cannot tell them
                    # apart, so both are named. Naming only the total one sent a
                    # reader to the wrong knob whenever a build went quiet for a
                    # minute — which an install downloading a large package does.
                    yield frame(
                        {
                            "type": "output",
                            "text": (
                                "\nThe build timed out and was killed. It has to finish "
                                "inside the sandbox's `exec_timeout`, and never go quiet for "
                                "longer than its `log_timeout` (60s each by default) — raise "
                                "them for this deployment, or give the build less to do.\n"
                            ),
                        }
                    )
                yield frame({"type": "done", "exit_code": result.exit_code})
            except Exception as exc:  # noqa: BLE001 — every path out posts a verdict
                # The headers went out with the first byte, so this can never be
                # a 500: it just ENDS the response. The page's `for await` then
                # falls through with nothing said — partial output and no
                # verdict, forever. A sentence and a non-zero code instead.
                logger.warning("wui: build stream failed: %s", exc)
                yield frame({"type": "output", "text": f"\nThe build could not finish: {exc}\n"})
                yield frame({"type": "done", "exit_code": EXIT_STREAM_BROKE})
            finally:
                # The reader navigated away (the server cancels this generator,
                # or closes it) — take the build down with them. Left alone, a
                # `pnpm install` keeps running against a folder nobody is
                # watching and the next open starts a second one beside it, both
                # writing the same `dist/`. One guard, in the one place every
                # exit passes through.
                if not task.done():
                    task.cancel()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/a/{slug}/items/{item_id}/wui/run")
    async def wui_run(slug: str, item_id: str, body: RunBody) -> StreamingResponse:
        """Start one run from the page and stream its events back.

        The SAME engine a schedule uses, started now instead of when the clock
        says so — because once a page's judgement can read files and call tools
        it is not "a question with an answer", it is a piece of work that takes
        minutes. Making it a run is what gives it a record, a stop button, and
        the progress the page draws, all of which already exist.

        The page gets the platform's events VERBATIM and decides what to show.
        The pane's own chrome does not know which row somebody clicked, and the
        whole premise of a WUI is that its author owns the experience.

        The verb is `execute`: this runs work in the item, like a notebook cell.
        """
        investigation_id = locator.require_access(slug, item_id, "execute")

        allowed = list(await workflows_for(investigation_id))
        if body.workflow not in allowed:
            # Named, because this reaches a person through the page's own error
            # panel and "which one, and why not" is all they can act on. The
            # list is the ONE every entrance consults (`workflow.offered`: the
            # profile's workflows plus the item's own `.workflows/`), so the
            # true cause is "this item has no such workflow" — not the "app does
            # not offer" the old wording said, which read as an authorisation an
            # operator had to grant when saving the workflow is all it takes.
            raise HTTPException(status_code=403, detail=no_such_workflow(body.workflow, allowed))
        # The item has it; will it run? A file that does not parse used to reach
        # `orchestrator.start`, fail an assertion and come back as a 502 "could
        # not be started" — the reason only in the log. Said here, the same
        # sentence the Run route and the agent's refusal use.
        problem = await workflow_problem_for(investigation_id, body.workflow)
        if problem is not None:
            raise HTTPException(status_code=422, detail=wont_parse(body.workflow, problem))

        # A REAL conversation, not a minted label. `chat_id` is looked up by
        # `workflow_exec.drive_turn`, and one that resolves to nothing falls back
        # to the item's DEFAULT chat — so the page's run would read the user's own
        # chat history as its context and append its turns there. It is also what
        # `active_run_for_chat` matches on, so an invented id quietly exempts
        # pages from the one-run-per-item rule.
        key = locator.open_run_chat(investigation_id, body.workflow)
        # SUBSCRIBE FIRST. A run that begins before anyone is listening loses its
        # opening events — exactly the ones that say something is happening — so
        # the page would show nothing at all for the first second of every call.
        stream = turn_engine.subscribe_sse(key)

        try:
            run_id = await orchestrator.start(
                slug=slug,
                item_id=investigation_id,
                profile=locator.profile_of(investigation_id),
                captured_user=locator.owner_of(investigation_id) or "",
                # Billed to the owner, but run with the PRESSER's private
                # values (`plan-wui-viewer-login` Q6) — never the owner's.
                env_user=get_user_id() if get_user_id is not None else "",
                workflow_id=body.workflow,
                chat_id=key,
                payload=body.payload,
            )
        except Exception as exc:
            # Take the chat back down with the run that never started. A chat with
            # no `run_id` is a FREE chat, and the earliest free chat is the item's
            # default — a refused run would otherwise install a new default
            # conversation for everyone on the item, once per retry.
            locator.settle_run_chat(key, None)
            # And release the stream. `subscribe_sse` attaches the subscriber
            # eagerly, before the generator is ever iterated, so a response we
            # never return leaves its queue and its session in memory for good.
            await turn_engine.forget(key)
            if isinstance(exc, HTTPException):
                raise
            # A 200 with an empty stream is indistinguishable from "still
            # thinking", and a page waiting on that waits forever. A refusal has
            # to arrive as a refusal.
            logger.exception("wui: item %s could not start %s", investigation_id, body.workflow)
            raise HTTPException(
                status_code=502, detail=f"{body.workflow} could not be started."
            ) from exc

        locator.settle_run_chat(key, run_id)
        return StreamingResponse(stream, media_type="text/event-stream")

    async def _prepare_tool(
        investigation_id: str, name: str, request: Request
    ) -> tuple[AgentToolContext, PackageInfo, CommandInfo]:
        """Which command ``name`` is for this item, and the context it runs in.

        Shared by `callTool` and `startTool` (#942) so a job reaches exactly the
        tools a call does, refused with the same sentences, run with the same
        environment — one door, two ways through it."""
        external = await _external(investigation_id)
        available = [*bundled, *external.packages]
        # A door (plan-tools-picker-groups part 2): the resolved config meets
        # the package list here, so the grant is finalized the same way a turn
        # finalizes it — a command the item's picker turned off is not held by
        # the agent, and not reachable from a page either.
        config = locator.resolve_agent_config(investigation_id)
        # The App's ceiling, kept aside before the pins are spent: it decides
        # which of two refusals below is true.
        offered = list(config.tool_ceiling) if config else []
        # Finalized ONCE, and that config is what the ctx below carries too:
        # provisioning reads `allowed_tools` off the ctx to decide which
        # bundles to install, and a door that admitted from the finished list
        # while the ctx held the pending one installed nothing for a command
        # it then ran.
        if config is not None:
            config = finalize_tool_grants(config, available)
        allowed = config.allowed_tools if config else []

        try:
            found = find_allowed_command(available, allowed, name)
        except ValueError as exc:
            # A bare name two granted third-party commands share: only this call
            # fails, with the full names to use instead — the same sentence the
            # model gets (`ambiguous_name_message`). A 409, not a 500, because it
            # reaches a person through the page's error panel and says what to do.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if found is None:
            # Named rather than 404'd blank: this reaches a person through the
            # page's own error panel, and "which tool, and why not" is the whole
            # of what they can act on.
            # The local name in `<local name>__<command>` or `<local name>:<command>`.
            # A bare name is a command name, so a refused tool's reason is only
            # found here when the page named the tool itself.
            local = (
                name.split(THIRD_PARTY_SEP, 1)[0]
                if THIRD_PARTY_SEP in name
                else name.partition(":")[0]
            )
            if reason := external.refused.get(local):
                raise HTTPException(status_code=409, detail=f"{name} is unavailable: {reason}")
            try:
                in_ceiling = find_allowed_command(available, offered, name) is not None
            except ValueError:
                # Several offered commands answer to it and none is held: the
                # item turned them off, which is the refusal that is true.
                in_ceiling = True
            if in_ceiling:
                # The App offers it; this item's picker turned it off. Naming
                # app.json here would send the person to the wrong switch.
                raise HTTPException(
                    status_code=403,
                    detail=f"{name} is turned off for this item — enable it in the tool picker.",
                )
            raise HTTPException(
                status_code=403,
                detail=f"This app does not offer {name} to its pages.",
            )
        pkg, command = found

        layers = locator.env_layers_of(investigation_id)
        session = await registry.session(investigation_id)
        ctx = AgentToolContext(
            investigation_id=investigation_id,
            sandbox=sandbox,
            sandbox_spec=SandboxSpec(tools=external.shas),
            tool_versions=external.versions(),
            packages=list(available),
            agent_config=config,
            prebuilt_dir=prebuilt_dir,
            # Resolved exactly as a turn resolves them (`turn_context`): the
            # person who pressed the button is the PRIVATE layer, the item's
            # copy the SHARED one, and the item's per-name policy picks — so a
            # page cannot reach a different system from the one the agent does.
            user_env=resolve(
                shared=layers.shared,
                person=await _request_env(request, investigation_id),
                policy=layers.policy,
            ),
            # The registry's own wake path, so this shares the item's ONE
            # sandbox with its turns rather than racing a second one into
            # existence beside it.
            ensure_sandbox_via=lambda on_progress, tools: registry.ensure_handle(
                session, tools=tools, on_progress=on_progress
            ),
            # #775: the item's preparation lock too — this shares the item's ONE
            # environment with its turns exactly as it shares their sandbox.
            prepare_env_via=lambda handle, on_output: registry.prepare_project_env(
                session, handle, on_output=on_output
            ),
        )
        return ctx, pkg, command

    @app.post("/a/{slug}/items/{item_id}/wui/tools/{name}/call")
    async def wui_call_tool(
        slug: str, item_id: str, name: str, body: CallToolBody, request: Request
    ) -> CallToolOut:
        # A tool can write, so the caller needs the verb that lets them write —
        # the same authority they would need to make the change by hand.
        investigation_id = locator.require_access(slug, item_id, "edit_content")

        ctx, pkg, command = await _prepare_tool(investigation_id, name, request)
        handle = await ctx.ensure_sandbox()
        result = await exec_package_command(ctx, handle, pkg, command.name, json.dumps(body.args))
        logger.info(
            "wui: item %s ran %s (exit %s)", investigation_id, command.name, result.exit_code
        )
        return CallToolOut(
            output=result.stdout.decode("utf-8", "replace"), exit_code=result.exit_code
        )

    # ── page jobs (#942): `callAgent`, `startAgent`, `startTool` ────────────
    async def _find_agent(investigation_id: str, name: str) -> SubagentDef:
        """One of the item's sub-agents — the ones its chat could delegate to
        (D2). Refused by name, with the ones there are, because this reaches a
        person through the page's error panel."""
        defs = list(await subagents_for(investigation_id)) if subagents_for else []
        for d in defs:
            if d.name == name:
                return d
        have = ", ".join(sorted(d.name for d in defs)) or "none"
        raise HTTPException(
            status_code=404,
            detail=f"This item has no sub-agent named {name}. It has: {have}.",
        )

    async def _start_job(
        investigation_id: str, job: PageJob, title: str, work_on: Callable[[str], _Work]
    ) -> str:
        """Open the job's own conversation, start the job on it, link the two (D5).

        Its OWN chat, as a page's `startRun` gets: the job's turns are recorded
        there and nowhere else, its Stop is that chat's, and two jobs never
        share one — which is what lets them run side by side (D6)."""
        key = locator.open_run_chat(investigation_id, title)
        try:
            run_id = await orchestrator.start_page_job(
                item_id=investigation_id,
                captured_user=locator.owner_of(investigation_id) or "",
                # Billed to the owner, run with the PRESSER's private values (D11).
                env_user=get_user_id() if get_user_id is not None else "",
                chat_id=key,
                job=job,
                work=work_on(key),
            )
        except TooManyPageJobs as exc:
            locator.settle_run_chat(key, None)
            raise HTTPException(
                status_code=429,
                detail=f"This item already has {exc.limit} page jobs running. "
                "Wait for one to finish, or stop one, then try again.",
            ) from exc
        except Exception as exc:
            # As `wui_run`: a chat with no run is a FREE chat, and the earliest
            # free chat becomes the item's default — take it back down.
            locator.settle_run_chat(key, None)
            logger.exception("wui: item %s could not start %s", investigation_id, job.name)
            raise HTTPException(
                status_code=502, detail=f"{job.name} could not be started."
            ) from exc
        locator.settle_run_chat(key, run_id)
        return run_id

    async def _start_agent(investigation_id: str, name: str, text: str) -> str:
        if drive_agent is None:
            raise HTTPException(status_code=501, detail="Sub-agents cannot be asked here.")
        defn = await _find_agent(investigation_id, name)
        owner = locator.owner_of(investigation_id) or ""
        drive = drive_agent

        def work_on(key: str) -> _Work:
            async def work(wf: Any) -> dict[str, Any]:
                output, failed = await drive(
                    investigation_id, key, owner, text, defn, run_id=wf.run_id
                )
                return {"output": output, "failed": failed}

            return work

        return await _start_job(
            investigation_id,
            PageJob(kind="agent", name=name, input=text),
            f"頁面：subagent {name}",
            work_on,
        )

    async def _start_tool(
        investigation_id: str, name: str, args: dict[str, Any], request: Request
    ) -> str:
        """`startTool` (#942 P4): `callTool`'s command, run as a job.

        Resolved and given its environment NOW, while the click's request is
        here — the same refusals and the same person's values a call gets. What
        differs is time: the command's deadline is the job's own, not the
        sandbox's minute, and a Stop must reach it from any pod."""
        ctx, pkg, command = await _prepare_tool(investigation_id, name, request)
        args_json = json.dumps(args)

        def work_on(key: str) -> _Work:
            async def work(wf: Any) -> dict[str, Any]:
                epoch = await turn_engine.cancel_epoch(key) if turn_engine else 0
                deadline = orchestrator.page_job_deadline(wf.run_id)

                async def run() -> ExecResult:
                    handle = await ctx.ensure_sandbox()
                    remaining = max(1.0, (deadline - now_ms()) / 1000)
                    return await exec_package_command(
                        ctx, handle, pkg, command.name, args_json, exec_timeout=remaining
                    )

                async def stopped_elsewhere() -> None:
                    # A Stop on another pod only advances this chat's turn
                    # epoch (`orchestrator.cancel` → `on_stop`). A turn notices
                    # that on its own; a command has no turn, so it is watched.
                    if turn_engine is None:
                        await asyncio.Event().wait()  # no signal to watch for
                    while turn_engine is not None:
                        await asyncio.sleep(_POLL_S)
                        if await turn_engine.cancel_epoch(key) > epoch:
                            return

                command_task = asyncio.ensure_future(run())
                watch = asyncio.ensure_future(stopped_elsewhere())
                try:
                    await asyncio.wait({command_task, watch}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    # However this is left — the epoch moved, or the job's own
                    # driver was cancelled by a Stop on THIS pod — the command
                    # goes with it. Cancelling the driver does not reach a task
                    # of its own, and a stopped job must not keep running.
                    watch.cancel()
                    if not command_task.done():
                        command_task.cancel()
                        # Settle it here, so nothing of it outlives the job.
                        await asyncio.wait({command_task})
                if command_task.cancelled():
                    raise asyncio.CancelledError
                result = command_task.result()
                logger.info(
                    "wui: item %s job ran %s (exit %s)",
                    investigation_id,
                    command.name,
                    result.exit_code,
                )
                return {
                    "output": result.stdout.decode("utf-8", "replace"),
                    "exit_code": result.exit_code,
                }

            return work

        return await _start_job(
            investigation_id,
            PageJob(kind="tool", name=name, input=args_json),
            f"頁面：工具 {name}",
            work_on,
        )

    async def _job_state(job_id: str, investigation_id: str) -> Any:
        """The job, IF it is a page job of this item — else 404."""
        try:
            return await orchestrator.page_job_state(job_id, investigation_id)
        except (ResourceIDNotFoundError, ResourceIsDeletedError) as exc:
            raise HTTPException(status_code=404, detail="This item has no such job.") from exc

    @app.post("/a/{slug}/items/{item_id}/wui/agents/{name}/call")
    async def wui_call_agent(
        slug: str, item_id: str, name: str, body: AgentCallBody
    ) -> AgentCallOut:
        """`callAgent` (#942 D7): ask one of the item's sub-agents and wait for
        its answer — the same shape as a tool call, `{input}` for `{args}`.

        It IS a job underneath (a run record, a Stop, a place in the item's
        quota), waited on for at most `CALL_TIMEOUT_S`. Past that the job is
        stopped rather than left running behind a page that has already been
        told it failed, and the sentence names the verb to use instead."""
        investigation_id = locator.require_access(slug, item_id, "execute")
        run_id = await _start_agent(investigation_id, name, body.input)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CALL_TIMEOUT_S
        state = await _job_state(run_id, investigation_id)
        while state.state == "running":
            if loop.time() >= deadline:
                await orchestrator.cancel(run_id, investigation_id)
                raise HTTPException(
                    status_code=504,
                    detail=f"{name} took longer than {CALL_TIMEOUT_S:g}s, so it was stopped. "
                    "Use startAgent for work that takes a while.",
                )
            await asyncio.sleep(_POLL_S)
            state = await _job_state(run_id, investigation_id)
        if state.state == "done":
            out = state.output or {}
            return AgentCallOut(output=str(out.get("output", "")), failed=bool(out.get("failed")))
        if state.state == "stopped":
            raise HTTPException(status_code=409, detail=f"{name} was stopped before it answered.")
        raise HTTPException(status_code=502, detail=state.error or f"{name} failed.")

    @app.post("/a/{slug}/items/{item_id}/wui/jobs")
    async def wui_start_job(slug: str, item_id: str, body: JobBody, request: Request) -> JobStarted:
        """`startTool` / `startAgent` (#942 D8): start the work and answer with
        its id at once; the page asks `GET …/jobs/{id}` how it is going."""
        investigation_id = locator.require_access(slug, item_id, "execute")
        if body.kind == "agent":
            if not isinstance(body.input, str):
                raise HTTPException(status_code=422, detail="A sub-agent's input is text.")
            return JobStarted(job_id=await _start_agent(investigation_id, body.name, body.input))
        if not isinstance(body.input, dict):
            raise HTTPException(status_code=422, detail="A tool's input is an object of args.")
        return JobStarted(
            job_id=await _start_tool(investigation_id, body.name, body.input, request)
        )

    @app.get("/a/{slug}/items/{item_id}/wui/jobs/{job_id}")
    async def wui_job_status(slug: str, item_id: str, job_id: str) -> JobOut:
        """`job.status()` — and what `job.result()` polls."""
        investigation_id = locator.require_access(slug, item_id, "execute")
        state = await _job_state(job_id, investigation_id)
        return JobOut(state=state.state, output=state.output, error=state.error)

    @app.post("/a/{slug}/items/{item_id}/wui/jobs/{job_id}/stop")
    async def wui_stop_job(slug: str, item_id: str, job_id: str) -> JobOut:
        """`job.stop()` — the same Stop the run's own chat has."""
        investigation_id = locator.require_access(slug, item_id, "execute")
        await _job_state(job_id, investigation_id)  # a page job of THIS item, or 404
        await orchestrator.cancel(job_id, investigation_id)
        state = await _job_state(job_id, investigation_id)
        return JobOut(state=state.state, output=state.output, error=state.error)

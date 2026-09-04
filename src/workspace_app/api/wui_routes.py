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
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.context import AgentToolContext
from ..sandbox.protocol import ExecResult, Sandbox, SandboxSpec
from ..tooling.external import ExternalTools
from ..tooling.registry import PackageInfo, exec_package_command, find_allowed_command
from .locator import ItemLocator
from .request_env import IRequestEnv
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


class CallToolOut(BaseModel):
    """What the page gets back.

    The tool's stdout is handed over verbatim: the caller is a program, so
    anything we appended to explain a truncation would arrive as part of its
    data. `exit_code` carries the tool's own contract unchanged."""

    output: str
    exit_code: int


def register_wui_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    sandbox: Sandbox,
    registry: Any,
    packages: list[PackageInfo] | None,
    prebuilt_dir: Path | None,
    resolve_external: Callable[[str], Any] | None = None,
    request_env: IRequestEnv | None = None,
    get_user_id: Callable[[], str] | None = None,
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

    async def _request_env(request: Request, item_id: str) -> dict[str, str]:
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
        if request_env is None:
            return {}
        uid = get_user_id() if get_user_id is not None else ""
        try:
            return await request_env.env_for(request, user_id=uid, item_id=item_id)
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

    async def _external(item_id: str) -> ExternalTools:
        if resolve_external is not None:
            return await resolve_external(item_id)
        return await resolve_item_tools(sandbox, locator, item_id)

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
        # which is exactly what `IRequestEnv` promises never happens ("the
        # values it returns are NEVER written back anywhere. They live for
        # exactly one turn").
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
        env = locator.env_vars_of(investigation_id)

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

    @app.post("/a/{slug}/items/{item_id}/wui/tools/{name}/call")
    async def wui_call_tool(
        slug: str, item_id: str, name: str, body: CallToolBody, request: Request
    ) -> CallToolOut:
        # A tool can write, so the caller needs the verb that lets them write —
        # the same authority they would need to make the change by hand.
        investigation_id = locator.require_access(slug, item_id, "edit_content")

        config = locator.resolve_agent_config(investigation_id)
        allowed = config.allowed_tools if config is not None else []

        external = await _external(investigation_id)
        available = [*bundled, *external.packages]

        try:
            found = find_allowed_command(available, allowed, name)
        except ValueError as exc:
            # Two packages exporting one command name: a deploy-configuration
            # fault, not this caller's. It breaks the agent's turn identically,
            # but an opaque 500 here reaches a person through the page's error
            # panel with nothing to act on.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if found is None:
            # Named rather than 404'd blank: this reaches a person through the
            # page's own error panel, and "which tool, and why not" is the whole
            # of what they can act on.
            if reason := external.refused.get(name.partition(":")[0]):
                raise HTTPException(status_code=409, detail=f"{name} is unavailable: {reason}")
            raise HTTPException(
                status_code=403,
                detail=f"This app does not offer {name} to its pages.",
            )
        pkg, command = found

        session = await registry.session(investigation_id)
        ctx = AgentToolContext(
            investigation_id=investigation_id,
            sandbox=sandbox,
            sandbox_spec=SandboxSpec(tools=external.shas),
            packages=list(available),
            agent_config=config,
            prebuilt_dir=prebuilt_dir,
            # The item's own win, exactly as they do in a turn
            # (`turn_context`): those are the ones a person set on purpose,
            # and a page must not be able to reach a different system from
            # the one the agent reaches.
            user_env={
                **await _request_env(request, investigation_id),
                **locator.env_vars_of(investigation_id),
            },
            # The registry's own wake path, so this shares the item's ONE
            # sandbox with its turns rather than racing a second one into
            # existence beside it.
            ensure_sandbox_via=lambda on_progress, tools: registry.ensure_handle(
                session, tools=tools, on_progress=on_progress
            ),
        )
        handle = await ctx.ensure_sandbox()
        result = await exec_package_command(ctx, handle, pkg, command.name, json.dumps(body.args))
        logger.info(
            "wui: item %s ran %s (exit %s)", investigation_id, command.name, result.exit_code
        )
        return CallToolOut(
            output=result.stdout.decode("utf-8", "replace"), exit_code=result.exit_code
        )

#!/usr/bin/env python
"""Live check (#549): does a REAL model actually stop misreading workspace paths?

The unit tests prove the tools now PRINT relative paths. They cannot prove the
thing the change was made for — that a real model, given the real prompt, stops
copying a listed path into the shell where a leading ``/`` means the system root.
Only a live model can show that, so this drives one real workspace turn against a
local Ollama, over a REAL ``LocalProcessSandbox`` (a mock sandbox stores paths in
a dict and would resolve ``/data/x.csv`` happily — it cannot reproduce the bug).

It is an A/B on purpose. Running only the fixed code would show "it works" without
showing that anything was broken, which is not evidence. So the same turn runs twice:

    new  — current code: `list_files` returns `data/readings.csv`
    old  — `rel_path` neutered to identity, reproducing the pre-#549 `/data/…`

The task is chosen so the model MUST carry a listed path into `exec`: it has to
count rows with python, not by eye. What we score, per arm:

    rooted    — an `exec` argv that carries a `/`-prefixed workspace path
    pathfail  — non-zero execs whose error is ABOUT the path (a missing file)
    otherfail — non-zero execs that failed for unrelated reasons
    correct   — the final answer contains the true row count

Splitting `pathfail` from `otherfail` is not cosmetic. A raw "failed execs" count
is dominated by two failure modes this change has nothing to do with: the model
cramming `with open(...)` into `python -c` (a SyntaxError — the very thing
`_sandbox.md` warns about, and it still does it), and inventing a filename that
was never in the listing. Lumping those in makes both arms look equally broken
and hides the one difference that matters.

Expectation: `old` produces rooted argv and path failures, `new` produces neither.
`otherfail` should be similar in both arms — it is the model's own sloppiness, not
ours. A small model is stochastic, so each arm runs `--runs` times; read the trend,
not a single sample.

Usage (needs a reachable Ollama with a tool-capable model):
    uv run python scripts/check_agent_relative_paths.py
    uv run python scripts/check_agent_relative_paths.py --model ollama_chat/qwen3:8b --runs 5
"""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from workspace_app.agent.context import AgentToolContext
from workspace_app.api.events import MessageDelta, ToolEnd, ToolStart
from workspace_app.api.litellm_runner import LitellmAgentRunner
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import AgentConfig, make_spec
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.protocol import SandboxHandle, SandboxSpec
from workspace_app.sync import SandboxSync

# A NESTED file, so the path the model must reuse is more than a bare name — that
# is where a wrong leading slash actually costs it.
_DATA_PATH = "data/readings.csv"
_ROWS = 7
_CSV = "zone,temp\n" + "".join(f"z{i},{200 + i}\n" for i in range(_ROWS))

# Forces a listed path into the shell: it may not eyeball the file, and the file
# tools alone cannot produce the count.
_PROMPT = (
    "There is one CSV of sensor readings in this workspace. Using python in the "
    "shell (not by reading the file yourself), count how many DATA rows it has, "
    "excluding the header. Reply with the number."
)

# `_format_exec`'s header — the only place an exit code is exposed (tools.py).
_EXIT_RE = re.compile(r"^Tool `(?P<tool>[\w:-]+)` returned \(exit_code=(?P<code>-?\d+)\):")

# A failure that is ABOUT the path — the shell or python could not find the file.
# Anything else (a SyntaxError from cramming into `python -c`, a missing module)
# is the model's own doing and must not be scored against this change.
_PATH_ERR_RE = re.compile(
    r"FileNotFoundError|No such file or directory|cannot open|can't open", re.I
)


@dataclass
class Outcome:
    rooted: list[str]  # exec argvs carrying a `/`-prefixed workspace path
    pathfail: int  # non-zero execs whose error is about a missing file
    otherfail: int  # non-zero execs that failed for any other reason
    execs: int
    answer: str

    @property
    def correct(self) -> bool:
        return str(_ROWS) in self.answer


def _carries_rooted_path(arg: str) -> bool:
    """Whether this argv element references the workspace file with a leading `/`.

    It is a SUBSTRING search, not a `startswith` on the token: in practice the
    model does not pass the path as its own argument, it embeds it in a program —
    `python -c "...open(\'/data/readings.csv\')..."`. Checking only whole tokens
    misses every real occurrence, which is exactly how the first version of this
    probe reported "clean" for a run that had just crashed on `/data/readings.csv`."""
    return f"/{_DATA_PATH}" in arg


async def _run_once(model: str, base_url: str, root: Path, verbose: bool = False) -> Outcome:
    spec = make_spec(default_user="op")
    sandbox = LocalProcessSandbox(root_dir=root, isolate=False)
    filestore = SpecstarFileStore(spec)
    sync = SandboxSync(filestore=filestore, sandbox=sandbox)
    holder: dict[str, SandboxHandle] = {}

    async def _resolve(ws: str) -> SandboxHandle | None:
        return holder.get(ws)

    files = WorkspaceFiles(filestore, sandbox, _resolve)
    # Seed while COLD so the wake restores it — the same path a real upload takes.
    await files.write("ws-live", _DATA_PATH, _CSV.encode())

    async def wake(on_progress=None) -> SandboxHandle:
        h = await sandbox.create(SandboxSpec())
        await sync.restore("ws-live", h, on_progress=on_progress)
        holder["ws-live"] = h
        return h

    ctx = AgentToolContext(
        investigation_id="ws-live",
        agent_config=AgentConfig(
            name="live-check",
            model=model,
            llm_base_url=base_url,
            system_prompt=_system_prompt(),
            allowed_tools=["list_files", "read_file", "write_file", "exec"],
        ),
        sandbox=sandbox,
        filestore=filestore,
        files=files,
        sync=sync,
        ensure_sandbox_via=wake,
    )

    rooted: list[str] = []
    pathfail = otherfail = execs = 0
    answer: list[str] = []
    pending: dict[str, list[str]] = {}

    runner = LitellmAgentRunner(max_turns=8)
    async for ev in runner.run(_PROMPT, ctx):
        if isinstance(ev, ToolStart):
            if verbose:
                print(f"    CALL {ev.name} {ev.args}"[:300])
            if ev.name == "exec":
                execs += 1
                cmd = ev.args.get("cmd") or []
                argv = [str(a) for a in cmd] if isinstance(cmd, list) else [str(cmd)]
                pending[ev.call_id] = argv
                if any(_carries_rooted_path(a) for a in argv):
                    rooted.append(" ".join(argv))
        elif isinstance(ev, ToolEnd):
            m = _EXIT_RE.match(ev.output)
            if m and m["tool"] == "exec" and m["code"] != "0":
                if _PATH_ERR_RE.search(ev.output):
                    pathfail += 1
                else:
                    otherfail += 1
            if verbose:
                print(f"    -> {ev.output.strip()[:300]}")
            pending.pop(ev.call_id, None)
        elif isinstance(ev, MessageDelta) and not ev.reasoning:
            answer.append(ev.text)

    try:
        h = holder.get("ws-live")
        if h is not None:
            await sandbox.kill(h)
    except Exception:  # noqa: BLE001 — teardown must never mask the result
        pass
    return Outcome(
        rooted=rooted,
        pathfail=pathfail,
        otherfail=otherfail,
        execs=execs,
        answer="".join(answer),
    )


def _system_prompt() -> str:
    """The REAL shipped workspace preamble — the whole point is to check the
    prompt the app actually sends, not a paraphrase."""
    from workspace_app.apps import catalog as app_catalog

    base = Path(app_catalog.__file__).parent
    return (
        (base / "_base.md").read_text(encoding="utf-8").rstrip()
        + "\n\n"
        + (base / "_sandbox.md").read_text(encoding="utf-8").rstrip()
    )


async def _arm(
    name: str, model: str, base_url: str, runs: int, old: bool, verbose: bool = False
) -> list[Outcome]:
    import workspace_app.agent.tools as tools_mod

    original = tools_mod.rel_path
    if old:
        # Reproduce pre-#549: the listing hands back the store's `/`-prefixed key.
        tools_mod.rel_path = lambda p: p  # ty: ignore[invalid-assignment]
    try:
        out: list[Outcome] = []
        for i in range(runs):
            root = Path(tempfile.mkdtemp(prefix=f"livecheck-{name}-{i}-"))
            try:
                res = await _run_once(model, base_url, root, verbose)
            finally:
                shutil.rmtree(root, ignore_errors=True)
            flag = "ROOTED" if res.rooted else "clean"
            ok = "correct" if res.correct else "WRONG"
            print(
                f"  run {i + 1}/{runs}: {flag}, {res.pathfail} path-fail / "
                f"{res.otherfail} other-fail of {res.execs} execs, {ok}"
                + (f"  <- {res.rooted[0]}" if res.rooted else "")
            )
            out.append(res)
        return out
    finally:
        tools_mod.rel_path = original


async def main_async(args: argparse.Namespace) -> None:
    for name, old in (("old (pre-#549)", True), ("new (current)", False)):
        print(f"\n== {name} ==")
        res = await _arm(name.split()[0], args.model, args.base_url, args.runs, old, args.verbose)
        rooted = sum(1 for r in res if r.rooted)
        wrong = sum(1 for r in res if not r.correct)
        pf = sum(r.pathfail for r in res)
        of = sum(r.otherfail for r in res)
        print(
            f"  TOTAL: {rooted}/{len(res)} runs used a rooted path | "
            f"{pf} path failures | {of} unrelated failures | "
            f"{wrong}/{len(res)} wrong answers"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="ollama_chat/qwen3:14b")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--verbose", action="store_true", help="dump every tool call + result")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()

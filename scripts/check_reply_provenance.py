#!/usr/bin/env python
"""Live check (#748): what a reply's record actually claims, end to end.

The unit tests cover `_TurnReducer` — what we store, given events. They cannot
cover the thing this feature turns on, because it lives outside our process:
whether the provider answers `stream_options.include_usage` with its OWN counts,
and whether litellm quietly substitutes its tokenizer's guess when it does not.
No local model answers, so the only way to exercise it is to stand in for the
one part that has to behave: the endpoint.

Everything else is real — the app, the runner, litellm, the persistence chain,
and the same `/chats` → `/conversation/{id}` door the FE reads through.

Two runs, because the difference is app-side config, not endpoint behaviour:

  A. `reports_usage: true` + an endpoint that answers when asked
     → the record keeps the PROVIDER's numbers, `exact` is derived true, the
       model that answered is named, and `generation_ms` excludes TTFT.
  B. `reports_usage` absent + an endpoint that stays silent
     → the record keeps NOTHING (never litellm's substitute), while the live
       line still has an estimate to show rather than `↑0 ↓0`.

⚠️ The fake's reported prompt count is DERIVED from the request, not a flat
magic number. A count below the app's own chars/4 estimate is read as "the
provider silently truncated my context" (#739) and the turn ends without a
reply — a double contradicting the contract it stands for. The completion count
is what carries the signature instead: 137 for a six-character reply is a number
no tokenizer would produce, so a substituted value is instantly tellable.

Usage:  uv run python scripts/check_reply_provenance.py
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

COMPLETION_TOKENS = 137
TTFT_MS = 1200

_FAKE_ENDPOINT = """
import asyncio, json, os, time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

PORT = int(os.environ["PORT"])
REPORTS_USAGE = os.environ["REPORTS_USAGE"] == "1"
TTFT_MS = int(os.environ["TTFT_MS"])
COMPLETION_TOKENS = int(os.environ["COMPLETION_TOKENS"])
USAGE_OUT = os.environ["USAGE_OUT"]
REPLY = "\\u91cf\\u6e2c\\u5b8c\\u6210\\u3002"

app = FastAPI()


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    want = bool((body.get("stream_options") or {}).get("include_usage"))
    model = body.get("model", "fake")
    # Derived, so it is always ABOVE the app's chars/4 estimate — see the module
    # docstring: a smaller number is read as a silent truncation.
    prompt_tokens = sum(len(str(m.get("content") or "")) for m in body.get("messages") or []) + 7
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": COMPLETION_TOKENS,
        "total_tokens": prompt_tokens + COMPLETION_TOKENS,
    }

    if not body.get("stream"):
        return {
            "id": "cmpl-fake", "object": "chat.completion", "created": int(time.time()),
            "model": model, "usage": usage,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY},
                         "finish_reason": "stop"}],
        }

    def chunk(delta, finish=None, usage=None, choices=None):
        p = {"id": "cmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
             "model": model,
             "choices": choices if choices is not None
                        else [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            p["usage"] = usage
        return "data: " + json.dumps(p) + "\\n\\n"

    async def gen():
        # TTFT: thinking before the first token. `generation_ms` must not include
        # it, or the same model looks slower on a longer prompt.
        await asyncio.sleep(TTFT_MS / 1000)
        yield chunk({"role": "assistant", "content": ""})
        pieces = list(REPLY)
        for i, piece in enumerate(pieces):
            if i:
                await asyncio.sleep(0.6 / max(1, len(pieces) - 1))
            yield chunk({"content": piece})
        yield chunk({}, finish="stop")
        if want and REPORTS_USAGE:
            with open(USAGE_OUT, "w") as fh:
                json.dump({"prompt": prompt_tokens, "completion": COMPLETION_TOKENS}, fh)
            yield chunk({}, choices=[], usage=usage)
        yield "data: [DONE]\\n\\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
"""

_CONFIG = """server:
  host: 127.0.0.1
  port: {app_port}

filestore:
  kind: memory

agents:
  presets:
    qwen3-local:
      model: openai/fake-model
{usage_line}      llm:
        base_url: http://127.0.0.1:{llm_port}/v1
        api_key: sk-fake
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _tools_dir(repo: Path) -> Path:
    """`.workspace-tools` is build output, so a git WORKTREE does not carry it —
    reach across to the checkout that ran `prebuild_tools.py`."""
    here = repo / ".workspace-tools"
    if here.is_dir():
        return here
    try:
        common = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--git-common-dir"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        return here
    return (repo / common).resolve().parent / ".workspace-tools"


def _req(method: str, url: str, body: dict | None = None, timeout: float = 60) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - localhost
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else raw.decode(errors="replace"))


def _wait(url: str, seconds: float = 150) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            _req("GET", url, timeout=5)
            return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"timed out waiting for {url}")


class _Check:
    def __init__(self) -> None:
        self.failed = 0

    def __call__(self, label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")
        if not ok:
            self.failed += 1


def _run(name: str, repo: Path, *, reports_usage: bool, check: _Check) -> dict:
    llm_port, app_port = _free_port(), _free_port()
    tmp = Path(tempfile.mkdtemp(prefix=f"provenance-{name}-"))
    (tmp / "endpoint.py").write_text(_FAKE_ENDPOINT)
    (tmp / "config.yaml").write_text(
        _CONFIG.format(
            app_port=app_port,
            llm_port=llm_port,
            usage_line="      reports_usage: true\n" if reports_usage else "",
        )
    )
    usage_out = tmp / "usage.json"

    env = dict(os.environ)
    env["WORKSPACE_APP_CONFIG"] = str(tmp / "config.yaml")
    env.setdefault("WORKSPACE_TOOLS_DIR", str(_tools_dir(repo)))
    env["WORKSPACE_LLM_LOG"] = "0"

    procs: list[subprocess.Popen] = []
    try:
        procs.append(
            subprocess.Popen(  # noqa: S603
                [sys.executable, str(tmp / "endpoint.py")],
                cwd=str(repo),
                env={
                    **env,
                    "PORT": str(llm_port),
                    "REPORTS_USAGE": "1" if reports_usage else "0",
                    "TTFT_MS": str(TTFT_MS),
                    "COMPLETION_TOKENS": str(COMPLETION_TOKENS),
                    "USAGE_OUT": str(usage_out),
                },
            )
        )
        _wait(f"http://127.0.0.1:{llm_port}/v1/models")
        procs.append(
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "workspace_app"], cwd=str(repo), env=env
            )
        )
        api = f"http://127.0.0.1:{app_port}/api"
        _wait(f"{api}/apps")

        _, item = _req("POST", f"{api}/a/rca/items", {"title": f"provenance {name}"})
        iid = item["resource_id"]
        status, _ = _req("POST", f"{api}/a/rca/items/{iid}/messages", {"content": "一句話回答就好"})
        check(f"[{name}] the send is accepted", status == 202, f"status={status}")

        # Read through the FE's own door, so a shape change breaks this too
        # rather than letting it quietly measure something else.
        deadline = time.monotonic() + 150
        reply = None
        while time.monotonic() < deadline and reply is None:
            _, chats = _req("GET", f"{api}/a/rca/items/{iid}/chats")
            for info in chats or []:
                _, env_body = _req("GET", f"{api}/conversation/{info['chat_id']}")
                data = env_body.get("data", env_body) if isinstance(env_body, dict) else {}
                done = [
                    m
                    for m in (data.get("messages") or [])
                    if m.get("role") == "assistant" and m.get("content")
                ]
                if done:
                    reply = done[-1]
            if reply is None:
                time.sleep(2)
        if reply is None:
            check(f"[{name}] a reply was persisted", False, "none arrived")
            return {}
        if usage_out.exists():
            reply["_sent"] = json.loads(usage_out.read_text())
        return reply
    finally:
        for proc in procs:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                proc.kill()


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    check = _Check()

    print("\n=== A. the endpoint answers when asked, and the preset vouches for it")
    a = _run("A", repo, reports_usage=True, check=check)
    if a:
        m = a.get("metrics") or {}
        sent = a.get("_sent") or {}
        print(f"     metrics = {json.dumps(m, ensure_ascii=False)}")
        check(
            "the record keeps the PROVIDER's counts, not a tokenizer's",
            bool(sent)
            and m.get("measured_prompt_tokens") == sent.get("prompt")
            and m.get("measured_completion_tokens") == sent.get("completion"),
            f"recorded {m.get('measured_prompt_tokens')}/{m.get('measured_completion_tokens')}"
            f" vs sent {sent.get('prompt')}/{sent.get('completion')}",
        )
        check("`exact` is derived true from them", m.get("exact") is True, str(m.get("exact")))
        check("the model that answered is named", bool(m.get("model")), str(m.get("model")))
        check(
            "the turn's wall clock is recorded",
            (m.get("elapsed_ms") or 0) > 0,
            str(m.get("elapsed_ms")),
        )
        check(
            f"generation time excludes the {TTFT_MS}ms TTFT",
            0 < (m.get("generation_ms") or 0) < (m.get("elapsed_ms") or 0),
            f"generation={m.get('generation_ms')} elapsed={m.get('elapsed_ms')}",
        )

    print("\n=== B. the endpoint stays silent and nobody vouched for it")
    b = _run("B", repo, reports_usage=False, check=check)
    if b:
        m = b.get("metrics") or {}
        print(f"     metrics = {json.dumps(m, ensure_ascii=False)}")
        check(
            "the record keeps NO token counts — not litellm's substitute",
            m.get("measured_prompt_tokens") is None and m.get("measured_completion_tokens") is None,
            f"{m.get('measured_prompt_tokens')}/{m.get('measured_completion_tokens')}",
        )
        check("`exact` is false", m.get("exact") is not True, str(m.get("exact")))
        check(
            "the live line still has an estimate to show, not 0/0",
            (m.get("prompt_tokens") or 0) > 0,
            f"{m.get('prompt_tokens')}/{m.get('completion_tokens')}",
        )
        check("the model is still named", bool(m.get("model")), str(m.get("model")))

    print()
    if check.failed:
        raise SystemExit(f"LIVE CHECK FAILED ({check.failed} assertion(s))")
    print("LIVE CHECK PASSED")


if __name__ == "__main__":
    main()

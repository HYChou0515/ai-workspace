#!/usr/bin/env python
"""Live check (docs/plan-ext-ai.md P5): 問外部AI, end to end.

Real: the app (`python -m workspace_app`, its own composition root and SPA,
`server.ext_ai_enabled: true`), the card in a real browser, the helper's own
loop (`ai_workspace_helper.app.make_controller`) driving its own Chromium, and
the site-file TEMPLATE as the company would start from it — only its `URL` set.

Stand-ins, and only these:
- the MODEL — an OpenAI-compatible endpoint: it asks the external AI once,
  then answers quoting what came back. It proves the mechanism, not that a
  model would choose to.
- the EXTERNAL AI — a local chat page the template has never seen: a sign-in
  first (signed in a moment later, as a person would), then a blank
  conversation whose answer comes a few words at a time, with a token found
  nowhere else. The AI's final answer quoting it is only possible if the
  answer really came back.

The person edits the AI's question before pressing 「問外部AI」: the saved
lookup must hold the EDITED question (D2).

Screenshots of the card in `<out>/` at 1280 and 390 px; a demo of the browser
to `<out>/ext-ai-demo.mp4` (ffmpeg).

Usage:  uv run python scripts/check_ext_ai.py [--out DIR]
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_helper_live", _HERE / "check_ai_workspace_helper.py"
)
assert _spec and _spec.loader
_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_live)

TOKEN = "EXT-9213"
QUESTION = "2 GB 的 CSV 每天讀一次,pandas 和 polars 哪個比較省記憶體?"
EDIT = " 請一句話回答。"

_ENDPOINT = r"""
import asyncio, json, os, time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

PORT = int(os.environ["PORT"])
TOKEN = os.environ["TOKEN"]
QUESTION = os.environ["QUESTION"]
app = FastAPI()


def last_user(messages):
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
            return str(c or "")
    return ""


def decide(body):
    tools = {t.get("function", {}).get("name") for t in body.get("tools") or []}
    said = last_user(body.get("messages"))
    if "ask_ext_ai" not in tools:
        return ("text", "polars")
    if TOKEN in said:
        return ("text", f"外部AI 的說法:用 Polars 比較省記憶體(它的回答標著 {TOKEN})。")
    return ("call", "ask_ext_ai", {"why": "想聽聽外部AI 對兩個做法的比較", "question": QUESTION})


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "fake-model", "object": "model"}]}


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    plan = decide(body)
    model = body.get("model", "fake")

    def chunk(delta, finish=None):
        p = {"id": "cmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
             "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return "data: " + json.dumps(p, ensure_ascii=False) + "\n\n"

    if not body.get("stream"):
        text = plan[1] if plan[0] == "text" else "ok"
        return {"id": "cmpl-fake", "object": "chat.completion", "created": int(time.time()),
                "model": model, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": text}}]}

    async def gen():
        await asyncio.sleep(0.8)
        yield chunk({"role": "assistant", "content": ""})
        if plan[0] == "text":
            for piece in [plan[1][i:i + 6] for i in range(0, len(plan[1]), 6)]:
                await asyncio.sleep(0.05)
                yield chunk({"content": piece})
            yield chunk({}, finish="stop")
        else:
            args = json.dumps(plan[2], ensure_ascii=False)
            yield chunk({"tool_calls": [{"index": 0, "id": "call_ext", "type": "function",
                         "function": {"name": plan[1], "arguments": ""}}]})
            for i in range(0, len(args), 12):
                piece = {"index": 0, "function": {"arguments": args[i:i + 12]}}
                yield chunk({"tool_calls": [piece]})
            yield chunk({}, finish="tool_calls")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
"""

# The external AI's page: a sign-in, then a blank conversation; Enter sends.
_CHAT_SITE = r"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["TOKEN"]
PAGE = '''<!doctype html><meta charset="utf-8"><title>Company AI</title>
<header><nav>New chat · History · Settings</nav></header>
<div id="login"><p>Sign in to Company AI</p><input type="password"></div>
<main id="chat" hidden><textarea id="prompt" rows="3" cols="80"></textarea></main>
<script>
const login = document.getElementById('login'), chat = document.getElementById('chat');
if (document.cookie.includes('signed=1')) { login.remove(); chat.hidden = false; }
else setTimeout(() => { document.cookie = 'signed=1'; location.reload(); }, 2500);
const words = 'Polars 比較省記憶體,它的欄式格式與惰性讀取 TOKEN 都有幫助'.split(' ');
document.getElementById('prompt').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.shiftKey) return;
  e.preventDefault();
  const q = e.target.value; e.target.value = '';
  let log = document.getElementById('log');
  if (!log) { log = document.createElement('div'); log.id = 'log'; chat.prepend(log); }
  const me = document.createElement('div'); me.innerText = q; log.append(me);
  const bot = document.createElement('div'); log.append(bot);
  const stop = document.createElement('button'); stop.innerText = 'Stop generating';
  document.body.append(stop);
  let i = 0;
  const next = () => {
    if (i < words.length) {
      bot.append((i ? ' ' : '') + words[i++]); setTimeout(next, 700); return;
    }
    stop.remove();
  };
  setTimeout(next, 1500);
});
</script>'''.replace('TOKEN', TOKEN)


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE.encode())


ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Site).serve_forever()
"""

_HELPER = r"""
import logging, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["HELPER_SRC"])
from ai_workspace_helper.app import make_controller
from ai_workspace_helper.chrome import Chrome
from ai_workspace_helper.ext_ai import load_site

logging.basicConfig(level=logging.INFO)
home, url = Path(os.environ["AI_WORKSPACE_HELPER_HOME"]), os.environ["APP_URL"]
site = Path(os.environ["SITE_FILE"])
make_controller(
    home, url, lambda: Chrome(home / "profile", url, channel="chromium", headless=True),
    load_site=lambda: load_site(site),
).run()
"""

_CONFIG = """server:
  host: 127.0.0.1
  port: {app_port}
  ext_ai_enabled: true

filestore:
  kind: memory

agents:
  presets:
    qwen3-local:
      model: openai/fake-model
      llm:
        base_url: http://127.0.0.1:{llm_port}/v1
        api_key: sk-fake
"""


def _no_overflow(page: Any) -> bool:
    """Nothing wider than the window, the card's buttons inside the card."""
    return page.evaluate(
        """() => {
          const doc = document.documentElement;
          const card = document.querySelector('.outside-lookup-card');
          if (!card) return false;
          const r = card.getBoundingClientRect();
          const inside = [...card.querySelectorAll('button')].every((b) => {
            const q = b.getBoundingClientRect();
            return q.width === 0 || (q.left >= r.left - 1 && q.right <= r.right + 1);
          });
          return doc.scrollWidth <= doc.clientWidth && r.right <= window.innerWidth + 1 && inside;
        }"""
    )


def _shoot(page: Any, card: Any, path: Path) -> None:
    """The card, whole: centred first, so the chat's sticky header is not over it."""
    card.evaluate("el => el.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(800)
    card.screenshot(path=str(path))


def _tour(base: str, iid: str, out: Path, check: Any) -> Path | None:
    from playwright.sync_api import sync_playwright

    videos = out / "video"
    shutil.rmtree(videos, ignore_errors=True)
    end = time.monotonic() + 60
    while (_live._req("GET", f"{base}/api/ai-workspace-helper/status")[1] or {}) != {
        "online": True,
        "ext_ai": True,
    }:
        if time.monotonic() > end:
            check("the helper checked in, able to ask the external AI", False, "not after 60 s")
            return None
        time.sleep(1)
    check("the helper checked in, able to ask the external AI", True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(videos),
            record_video_size={"width": 1280, "height": 800},
            locale="zh-TW",
        )
        ctx.add_init_script(_live._CURSOR)
        page = ctx.new_page()
        try:
            page.goto(f"{base}/a/rca/{iid}", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            for dismiss in ("Got it", "知道了", "開始使用"):
                b = page.get_by_role("button", name=dismiss)
                if b.count():
                    b.first.click()
            box = page.locator("textarea").last
            box.wait_for(timeout=30_000)
            _live._glide(page, box)
            box.click()
            box.type("幫我比較 pandas 和 polars", delay=40)
            page.wait_for_timeout(400)
            box.press("Enter")

            go = page.get_by_role("button", name="問外部AI", exact=True)
            go.wait_for(timeout=90_000)
            question = page.get_by_label("要問的問題")
            check("the card shows the AI's question, editable", question.input_value() == QUESTION)
            card = page.locator(".outside-lookup-card").last
            _shoot(page, card, out / "card-1280.png")
            check("the card fits at 1280 px", _no_overflow(page))

            # the same card at phone width (feedback: measure more than one width).
            # Below 950 px the chat and the files take turns: hide the files
            # (View → Workspace), as a person would.
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(1200)
            page.get_by_role("button", name="View", exact=True).click()
            page.get_by_role("checkbox", name="Workspace").uncheck()
            page.keyboard.press("Escape")
            page.wait_for_timeout(1200)
            _shoot(page, card, out / "card-390.png")
            check("the card fits at 390 px", _no_overflow(page))
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(1200)

            _live._glide(page, question)
            question.click()
            question.press("End")
            question.type(EDIT, delay=40)
            _live._glide(page, go)
            go.click()

            status = page.get_by_role("status").filter(has_text="外部AI")
            status.wait_for(timeout=30_000)
            page.wait_for_timeout(3000)
            _shoot(page, card, out / "card-asking-1280.png")
            check(
                "while it asks, the card says so and for how long",
                "已等" in status.inner_text(),
                status.inner_text(),
            )

            page.get_by_text(f"標著 {TOKEN}").wait_for(timeout=180_000)
            check(f"the AI's answer quotes {TOKEN} — only in the external AI's answer", True)
            page.wait_for_timeout(2500)
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(2000)
        except Exception as exc:  # noqa: BLE001 — reported, with a screenshot
            page.screenshot(path=str(out / "failure.png"), full_page=True)
            check("the tour", False, f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        finally:
            video = page.video.path() if page.video else None
            ctx.close()
            browser.close()
    return Path(video) if video else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Live check of 問外部AI")
    parser.add_argument("--out", type=Path, default=Path(tempfile.mkdtemp(prefix="ext-ai-")))
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    repo = _HERE.parent
    check = _live._Check()

    subprocess.run(  # noqa: S603 — the converter the helper ships, built from the frontend
        ["pnpm", "run", "build:ai-workspace-helper"],  # noqa: S607
        cwd=repo / "web",
        check=True,
        capture_output=True,
    )
    llm_port, app_port, chat_port = _live._free_port(), _live._free_port(), _live._free_port()
    work = Path(tempfile.mkdtemp(prefix="ext-ai-live-"))
    for name, text in (("endpoint.py", _ENDPOINT), ("chat.py", _CHAT_SITE), ("helper.py", _HELPER)):
        (work / name).write_text(text, encoding="utf-8")
    # the company's file: the template, its URL set and nothing else
    template = (repo / "ai-workspace-helper" / "ext_ai_site.template.py").read_text(
        encoding="utf-8"
    )
    site_file = work / "ext_ai_site.py"
    site_file.write_text(
        template.replace('URL = "https://"', f'URL = "http://127.0.0.1:{chat_port}/new"'),
        encoding="utf-8",
    )
    (work / "config.yaml").write_text(
        _CONFIG.format(app_port=app_port, llm_port=llm_port), encoding="utf-8"
    )
    env = {
        **os.environ,
        "WORKSPACE_APP_CONFIG": str(work / "config.yaml"),
        "WORKSPACE_TOOLS_DIR": os.environ.get("WORKSPACE_TOOLS_DIR", str(_live._tools_dir(repo))),
        "WORKSPACE_LLM_LOG": "0",
        "TOKEN": TOKEN,
        "QUESTION": QUESTION,
    }
    base = f"http://127.0.0.1:{app_port}"
    procs: list[subprocess.Popen] = []

    def start(argv: list[str], **extra: str) -> None:
        log = open(work / f"{Path(argv[-1]).stem}.log", "w")  # noqa: SIM115
        procs.append(
            subprocess.Popen(  # noqa: S603
                argv, cwd=str(repo), env={**env, **extra}, stdout=log, stderr=subprocess.STDOUT
            )
        )

    try:
        start([sys.executable, str(work / "chat.py")], PORT=str(chat_port))
        start([sys.executable, str(work / "endpoint.py")], PORT=str(llm_port))
        _live._wait(f"http://127.0.0.1:{llm_port}/v1/models")
        start([sys.executable, "-m", "workspace_app"])
        _live._wait(f"{base}/api/apps")
        start(
            [sys.executable, str(work / "helper.py")],
            HELPER_SRC=str(repo / "ai-workspace-helper" / "src"),
            AI_WORKSPACE_HELPER_HOME=str(work / "helper-home"),
            APP_URL=base,
            SITE_FILE=str(site_file),
        )
        print(f"\napp {base} · logs in {work}", flush=True)

        _, item = _live._req("POST", f"{base}/api/a/rca/items", {"title": "問外部AI live check"})
        iid = item["resource_id"]
        video = _tour(base, iid, out, check)

        _, tree = _live._req("GET", f"{base}/api/a/rca/items/{iid}/tree")
        files = tree.get("files", []) if isinstance(tree, dict) else []
        lookups = sorted(f["path"].lstrip("/") for f in files if "lookups/" in f["path"])
        check("one answer was saved to the workspace", len(lookups) == 1, ", ".join(lookups))
        text = "".join(
            str(_live._req("GET", f"{base}/api/a/rca/items/{iid}/files/{urllib.parse.quote(p)}")[1])
            for p in lookups
        )
        check("it is saved as the external AI's answer", text.startswith("# 問外部AI:"))
        check("…asked by the helper", "由小幫手問" in text)
        check("the question sent is the one the person edited (D2)", QUESTION + EDIT in text)
        check(f"the external AI's {TOKEN} is in it", TOKEN in text)
        check("the company's address is not (D12)", f"127.0.0.1:{chat_port}" not in text)

        if video and shutil.which("ffmpeg"):
            mp4 = out / "ext-ai-demo.mp4"
            subprocess.run(  # noqa: S603
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(video),
                    "-c:v",
                    "libx264",  # noqa: S607
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(mp4),
                ],
                check=True,
            )
            print(f"\ndemo: {mp4}", flush=True)
    finally:
        for proc in reversed(procs):
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()

    print(f"\n{'FAILED' if check.failed else 'all ok'} ({check.failed} failed)")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())

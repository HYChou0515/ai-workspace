#!/usr/bin/env python
"""Live check (docs/plan-lookup-target-choice.md P6): the person picks the
search target, the AI only suggests one, the helper searches the one picked.

Real: the app (`python -m workspace_app`, its own composition root and SPA,
two `server.lookup_targets`), the card in a real browser, and the helper's own
loop (`ai_workspace_helper.app.make_controller`) driving its own Chromium.

Stand-ins, and only these:
- the MODEL — an OpenAI-compatible endpoint: it calls `ask_outside` suggesting
  the parts target, then answers quoting what came back. It proves the
  mechanism, not that a model would choose that target.
- the two SEARCH SITES — one local page each. Only the parts page carries
  TOKEN, so the AI's answer quoting it is only possible if the helper searched
  the target the PERSON picked (the first, preselected, would not have it).

1. No helper yet: the card by hand — a new-tab button per target with its
   description, none for the `preview: false` one.
2. The helper connected: a 「搜尋目標」 radio group, the first chosen (not the
   AI's); the person picks the parts target, the button follows, the helper
   fetches it, the answer is saved with its target and the AI quotes TOKEN.

Screenshots of the card in `<out>/` at 1280 and 390 px; a demo to
`<out>/lookup-targets-demo.mp4` (ffmpeg).

Usage:  uv run python scripts/check_lookup_targets.py [--out DIR]
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

TOKEN = "PART-7731"
PART = "A123-4567"
PARTS = "料號查詢"
WEB = "網頁搜尋"
DESCRIPTION = "公司零件資料庫,只認完整料號,例如 A123-4567。"

_ENDPOINT = r"""
import asyncio, json, os, time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

PORT = int(os.environ["PORT"])
TOKEN = os.environ["TOKEN"]
PART = os.environ["PART"]
PARTS = os.environ["PARTS"]
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
    if "ask_outside" not in tools:
        return ("text", "ok")
    if TOKEN in said:
        return ("text", f"料號 {PART} 查到了:庫存 12 件(資料庫的回答標著 {TOKEN})。")
    args = {"why": "要查這個料號的庫存", "query": PART, "target": PARTS}
    return ("call", "ask_outside", args)


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
            yield chunk({"tool_calls": [{"index": 0, "id": "call_look", "type": "function",
                         "function": {"name": plan[1], "arguments": ""}}]})
            for i in range(0, len(args), 12):
                piece = {"index": 0, "function": {"arguments": args[i:i + 12]}}
                yield chunk({"tool_calls": [piece]})
            yield chunk({}, finish="tool_calls")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
"""

# Two search sites: a web search with no answer, and the parts database, whose
# page alone carries TOKEN.
_SITES = r"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = os.environ["TOKEN"]


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/parts":
            pn = q.get("pn", [""])[0]
            body = (f"<title>{pn} - Parts</title><h1>{pn}</h1>"
                    f"<p>庫存 12 件 · 倉位 B-03 · {TOKEN}</p>")
        else:
            s = q.get("q", [""])[0]
            body = f"<title>{s} - Search</title><p>沒有找到「{s}」的結果。</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><meta charset=utf-8>{body}".encode())


ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Site).serve_forever()
"""

_CONFIG = """server:
  host: 127.0.0.1
  port: {app_port}
  lookup_targets:
    - name: {web}
      url: "http://127.0.0.1:{site_port}/search?q={{q}}"
    - name: {parts}
      url: "http://127.0.0.1:{site_port}/parts?pn={{q}}"
      description: {description}
      preview: false

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
          const inside = [...card.querySelectorAll('button, label')].every((b) => {
            const q = b.getBoundingClientRect();
            return q.width === 0 || (q.left >= r.left - 1 && q.right <= r.right + 1);
          });
          return doc.scrollWidth <= doc.clientWidth && r.right <= window.innerWidth + 1 && inside;
        }"""
    )


def _part(page: Any, card: Any) -> Any:
    """The part of the card that shows the targets: with the helper, its section
    (the 「搜尋目標」 radio group); by hand, the search section (the buttons)."""
    helper = card.get_by_test_id("outside-lookup-helper")
    if helper.count():
        return helper
    return card.locator(".outside-lookup-section").filter(has=page.get_by_label("搜尋關鍵字"))


def _shoot(page: Any, card: Any, path: Path) -> None:
    """That part, centred so the chat's sticky header and composer are not over
    it, with the drawn cursor moved off it."""
    part = _part(page, card)
    part.evaluate("el => el.scrollIntoView({block: 'center'})")
    page.mouse.move(2, 2)
    page.wait_for_timeout(800)
    part.screenshot(path=str(path))


def _at_phone_width(page: Any, card: Any, path: Path, check: Any, what: str) -> None:
    """The same card at 390 px (feedback: measure more than one width). Below
    950 px the chat and the files take turns: the files hidden, as a person would."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(1200)
    view = page.get_by_role("button", name="View", exact=True)
    if view.count():
        view.click()
        box = page.get_by_role("checkbox", name="Workspace")
        if box.count() and box.is_checked():
            box.uncheck()
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
    _shoot(page, card, path)
    check(f"{what} fits at 390 px", _no_overflow(page))
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(1200)


def _helper_online(base: str, seconds: float = 60) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (_live._req("GET", f"{base}/api/ai-workspace-helper/status")[1] or {}).get("online"):
            return True
        time.sleep(1)
    return False


def _tour(base: str, iid: str, out: Path, check: Any, start_helper: Any) -> Path | None:
    from playwright.sync_api import sync_playwright

    videos = out / "video"
    shutil.rmtree(videos, ignore_errors=True)
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
            box.type(f"幫我查料號 {PART} 的庫存", delay=40)
            page.wait_for_timeout(400)
            box.press("Enter")

            # ── 1. no helper: the card by hand ──
            card = page.locator(".outside-lookup-card").last
            web = page.get_by_role("button", name=f"{WEB} search(在新分頁開啟)")
            web.wait_for(timeout=90_000)
            check("by hand: the web target opens in a new tab", web.is_enabled())
            check(
                "by hand: no new-tab button for the preview:false target (T5)",
                card.get_by_role("button", name=PARTS).count() == 0,
            )
            web.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1500)
            _live._glide(page, web)
            page.wait_for_timeout(1500)
            _shoot(page, card, out / "by-hand-1280.png")
            check("the card by hand fits at 1280 px", _no_overflow(page))
            _at_phone_width(page, card, out / "by-hand-390.png", check, "the card by hand")

            # ── 2. the helper connected ──
            start_helper()
            check("the helper checked in", _helper_online(base))
            page.reload(wait_until="domcontentloaded")
            group = page.get_by_role("radiogroup", name="搜尋目標")
            group.wait_for(timeout=60_000)
            card = page.locator(".outside-lookup-card").last
            first = group.get_by_role("radio", name=WEB)
            pick = group.get_by_role("radio", name=PARTS)
            check("with the helper: the first target is chosen", first.is_checked())
            check("…not the AI's", not pick.is_checked())
            group.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1500)
            _shoot(page, card, out / "helper-1280.png")
            check("the card with the helper fits at 1280 px", _no_overflow(page))
            _at_phone_width(page, card, out / "helper-390.png", check, "the card with the helper")

            keywords = page.get_by_label("搜尋關鍵字")
            keywords.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(600)
            _live._glide(page, keywords)
            keywords.click()
            keywords.type(PART, delay=60)
            _live._glide(page, pick)
            pick.click()
            go = page.get_by_role("button", name=f"{PARTS} search", exact=True)
            check("the button follows the person's choice", go.count() == 1)
            go.evaluate("el => el.scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1200)
            _live._glide(page, go)
            go.click()

            page.get_by_text(f"標著 {TOKEN}").wait_for(timeout=180_000)
            check(f"the AI's answer quotes {TOKEN} — only on the target the person picked", True)
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
    parser = argparse.ArgumentParser(description="Live check of choosing a search target")
    parser.add_argument("--out", type=Path, default=Path(tempfile.mkdtemp(prefix="targets-")))
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
    llm_port, app_port, site_port = _live._free_port(), _live._free_port(), _live._free_port()
    work = Path(tempfile.mkdtemp(prefix="targets-live-"))
    for name, text in (
        ("endpoint.py", _ENDPOINT),
        ("sites.py", _SITES),
        ("helper.py", _live._HELPER),
    ):
        (work / name).write_text(text, encoding="utf-8")
    (work / "config.yaml").write_text(
        _CONFIG.format(
            app_port=app_port,
            llm_port=llm_port,
            site_port=site_port,
            web=WEB,
            parts=PARTS,
            description=DESCRIPTION,
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "WORKSPACE_APP_CONFIG": str(work / "config.yaml"),
        "WORKSPACE_TOOLS_DIR": os.environ.get("WORKSPACE_TOOLS_DIR", str(_live._tools_dir(repo))),
        "WORKSPACE_LLM_LOG": "0",
        "TOKEN": TOKEN,
        "PART": PART,
        "PARTS": PARTS,
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

    def start_helper() -> None:
        start(
            [sys.executable, str(work / "helper.py")],
            HELPER_SRC=str(repo / "ai-workspace-helper" / "src"),
            AI_WORKSPACE_HELPER_HOME=str(work / "helper-home"),
            APP_URL=base,
        )

    try:
        start([sys.executable, str(work / "sites.py")], PORT=str(site_port))
        start([sys.executable, str(work / "endpoint.py")], PORT=str(llm_port))
        _live._wait(f"http://127.0.0.1:{llm_port}/v1/models")
        start([sys.executable, "-m", "workspace_app"])
        _live._wait(f"{base}/api/apps")
        print(f"\napp {base} · logs in {work}", flush=True)

        _, item = _live._req("POST", f"{base}/api/a/rca/items", {"title": "搜尋目標 live check"})
        iid = item["resource_id"]
        video = _tour(base, iid, out, check, start_helper)

        _, tree = _live._req("GET", f"{base}/api/a/rca/items/{iid}/tree")
        files = tree.get("files", []) if isinstance(tree, dict) else []
        lookups = sorted(f["path"].lstrip("/") for f in files if "lookups/" in f["path"])
        check("one answer was saved to the workspace", len(lookups) == 1, ", ".join(lookups))
        text = "".join(
            str(_live._req("GET", f"{base}/api/a/rca/items/{iid}/files/{urllib.parse.quote(p)}")[1])
            for p in lookups
        )
        check("…fetched by the helper", "由小幫手抓取" in text)
        check(f"…from the target the person picked (「- 用:{PARTS}」)", f"- 用:{PARTS}" in text)
        check(f"…whose page carries {TOKEN}", TOKEN in text)

        if video and shutil.which("ffmpeg"):
            mp4 = out / "lookup-targets-demo.mp4"
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
                    "-crf",
                    "23",
                    "-movflags",
                    "+faststart",
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-an",
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

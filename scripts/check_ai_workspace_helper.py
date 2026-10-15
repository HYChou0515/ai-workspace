#!/usr/bin/env python
"""Live check (docs/plan-outside-helper.md P10): the helper, end to end.

Real: the app (`python -m workspace_app`, its own composition root and SPA), the
card in a real browser, the helper's own loop (`ai_workspace_helper.app.
make_controller` — what the window runs) driving its own Chromium, the page
converter built from the frontend, the lookups saved to the workspace.

Stand-ins, and only these:
- the MODEL — no local model answers, so an OpenAI-compatible endpoint plays
  it: search when asked, open the link the search brought back, then answer
  quoting the page. It proves the mechanism, not that a model would choose to.
- the WEB — a local site: a results page whose links are RELATIVE (as Google's
  raw HTML is) and an article carrying a token found nowhere else. The final
  answer quoting the token is only possible if the article really came back.
- the helper's Chromium runs headless here (no display); on the PC it is the
  person's Chrome, headed.

The person presses 「查」 ONCE. The second lookup — a link the first one brought
back — must go to the helper with no nod (D12).

Writes a demo of the browser to `<out>/ai-workspace-helper-demo.mp4` (ffmpeg).

Usage:  uv run python scripts/check_ai_workspace_helper.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TOKEN = "COW-4471"
QUERY = "pandas 2.0 新功能"

_ENDPOINT = r"""
import asyncio, json, os, time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

PORT = int(os.environ["PORT"])
SITE = os.environ["SITE"]
TOKEN = os.environ["TOKEN"]
QUERY = os.environ["QUERY"]
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
        return ("text", "pandas 2.0")
    if TOKEN in said:
        return ("text", f"查到了:pandas 2.0 的版本說明裡標著 {TOKEN},重點是 Copy-on-Write。")
    if f"{SITE}/article" in said:
        why = "結果裡第一筆是版本說明,打開看細節"
        return ("call", "ask_outside", {"why": why, "url": f"{SITE}/article"})
    return ("call", "ask_outside", {"why": "我需要 pandas 2.0 的最新說明", "query": QUERY})


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
        await asyncio.sleep(0.8)  # thinking: the "running" state is worth seeing
        yield chunk({"role": "assistant", "content": ""})
        if plan[0] == "text":
            for piece in [plan[1][i:i + 6] for i in range(0, len(plan[1]), 6)]:
                await asyncio.sleep(0.05)
                yield chunk({"content": piece})
            yield chunk({}, finish="stop")
        else:
            args = json.dumps(plan[2], ensure_ascii=False)
            call_id = "call_" + ("open" if "url" in plan[2] else "search")
            yield chunk({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                         "function": {"name": plan[1], "arguments": ""}}]})
            for i in range(0, len(args), 12):  # arguments arrive in pieces, as for real
                piece = {"index": 0, "function": {"arguments": args[i:i + 12]}}
                yield chunk({"tool_calls": [piece]})
            yield chunk({}, finish="tool_calls")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
"""

_SITE = r"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = os.environ["TOKEN"]


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/search":
            q = parse_qs(url.query).get("q", [""])[0]
            body = (
                f"<title>{q} - Search</title><h1>{q}</h1>"
                "<div><h3><a href='/article'>What's new in 2.0.0 (April 3, 2023)</a></h3>"
                "<p>Copy-on-Write improvements, pyarrow-backed dtypes and more.</p></div>"
                "<div><h3><a href='/forum'>pandas 2.0 breaking changes?</a></h3></div>"
            )
        elif url.path == "/article":
            body = (
                "<title>What's new in 2.0.0</title><h1>What's new in 2.0.0</h1>"
                f"<p>Release marker <b>{TOKEN}</b>: Copy-on-Write is now available.</p>"
                "<ul><li>Index holds numpy numeric dtypes</li><li>pyarrow engine</li></ul>"
            )
        else:
            self.send_response(404); self.end_headers(); self.wfile.write(b"<h1>Not Found</h1>")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Site).serve_forever()
"""

_HELPER = r"""
import logging, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["HELPER_SRC"])
from ai_workspace_helper.app import make_controller
from ai_workspace_helper.chrome import Chrome

logging.basicConfig(level=logging.INFO)
home, url = Path(os.environ["AI_WORKSPACE_HELPER_HOME"]), os.environ["APP_URL"]
make_controller(
    home, url, lambda: Chrome(home / "profile", url, channel="chromium", headless=True)
).run()
"""

_CONFIG = """server:
  host: 127.0.0.1
  port: {app_port}
  ai_workspace_helper_dir: "{release_dir}"
  lookup_targets:
    - name: Google
      url: "http://127.0.0.1:{site_port}/search?q={{q}}"

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

# A visible cursor in the recording (the page's own, not the OS's).
_CURSOR = """
window.addEventListener('DOMContentLoaded', () => {
  const dot = document.createElement('div');
  dot.style.cssText = 'position:fixed;z-index:2147483647;width:18px;height:18px;border-radius:50%;'
    + 'background:rgba(220,50,50,.55);border:2px solid #fff;pointer-events:none;'
    + 'transform:translate(-50%,-50%);left:-40px;top:-40px;transition:left .05s,top .05s';
  document.body.appendChild(dot);
  document.addEventListener('mousemove', e => {
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
  }, true);
});
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _tools_dir(repo: Path) -> Path:
    here = repo / ".workspace-tools"
    if here.is_dir():
        return here
    common = subprocess.run(  # noqa: S603
        ["git", "rev-parse", "--git-common-dir"],  # noqa: S607
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return (repo / common).resolve().parent / ".workspace-tools"


def _req(method: str, url: str, body: dict | None = None, timeout: float = 30) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - localhost
            raw = resp.read()
            try:
                return resp.status, (json.loads(raw) if raw else None)
            except ValueError:
                return resp.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, raw.decode(errors="replace")


def _wait(url: str, seconds: float = 180) -> None:
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
        print(
            f"  {'ok  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}", flush=True
        )
        if not ok:
            self.failed += 1


def _glide(page: Any, locator: Any) -> None:
    box = locator.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)
        page.wait_for_timeout(400)


def _tour(base: str, iid: str, out: Path, check: _Check) -> Path | None:
    from playwright.sync_api import sync_playwright

    videos = out / "video"
    shutil.rmtree(videos, ignore_errors=True)
    # The person's browser is already signed in and the helper already running
    # when they open the page — so is it here before recording starts.
    end = time.monotonic() + 60
    while _req("GET", f"{base}/api/ai-workspace-helper/status")[1] != {"online": True}:
        if time.monotonic() > end:
            check("the helper checked in", False, "not online after 60 s")
            return None
        time.sleep(1)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(videos),
            record_video_size={"width": 1280, "height": 800},
            locale="zh-TW",
        )
        ctx.add_init_script(_CURSOR)
        page = ctx.new_page()
        try:
            # 1. the download page: published, and this person's helper is connected
            page.goto(f"{base}/ai-workspace-helper", wait_until="domcontentloaded")
            page.get_by_text("小幫手已連線").wait_for(timeout=60_000)
            check("the 小幫手 page says the helper is connected", True)
            link = page.get_by_role("link", name="ai-workspace-helper-0.1.0.zip", exact=False)
            check("…and offers the published zip", link.count() == 1)
            _glide(page, link)
            page.wait_for_timeout(1500)

            # 2. the item: ask, and press 「查」 once
            page.goto(f"{base}/a/rca/{iid}", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            for dismiss in ("Got it", "知道了", "開始使用"):
                b = page.get_by_role("button", name=dismiss)
                if b.count():
                    b.first.click()
            box = page.locator("textarea").last
            box.wait_for(timeout=30_000)
            _glide(page, box)
            box.click()
            box.type("pandas 2.0 有什麼新功能?", delay=40)
            page.wait_for_timeout(400)
            box.press("Enter")

            go = page.get_by_role("button", name="查", exact=True)
            go.wait_for(timeout=90_000)
            page.wait_for_timeout(1200)
            # the box starts empty: the person copies the AI's suggestion and
            # pastes it (plan-outside-lookup D13)
            keywords = page.get_by_label("搜尋關鍵字")
            suggested = page.locator(".outside-lookup-card code").first.inner_text()
            _glide(page, keywords)
            keywords.fill(suggested)
            page.wait_for_timeout(400)
            _glide(page, go)
            go.click()
            check("the search card offered 「查」 and took the nod", True)

            # 3. nothing else: the helper searches, the AI opens the link it
            #    brought back (no second nod), and answers from the page
            page.get_by_text(f"標著 {TOKEN}").wait_for(timeout=180_000)
            check(f"the AI's answer quotes {TOKEN} — only on the page the helper opened", True)
            page.wait_for_timeout(3000)
            page.mouse.wheel(0, 600)
            page.wait_for_timeout(2500)
        except Exception as exc:  # noqa: BLE001 — reported, with a screenshot
            page.screenshot(path=str(out / "failure.png"), full_page=True)
            check("the tour", False, f"{type(exc).__name__}: {str(exc).splitlines()[0]}")
        finally:
            video = page.video.path() if page.video else None
            ctx.close()
            browser.close()
    return Path(video) if video else None


def _update_check(repo: Path, wheels: Path, check: _Check) -> None:
    """§3.7.5 (D21) for real on this machine: install 0.1.0 from a wheelhouse,
    then 0.2.0 over it as install.pyw does (a real venv, real pip, the real
    self-test), and the launcher takes 0.2.0 on the next start. Linux wheels
    (`wheels`: playwright and its dependencies for this machine) stand in for
    the Windows ones; only the Windows shortcuts are skipped."""
    import importlib.util

    sys.path.insert(0, str(repo / "ai-workspace-helper" / "src"))
    from ai_workspace_helper import setup_core  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "_pack", repo / "scripts" / "pack_ai_workspace_helper.py"
    )
    assert spec and spec.loader
    pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pack)

    work = Path(tempfile.mkdtemp(prefix="ai-workspace-helper-update-"))
    home = work / "AIWorkspaceHelper"

    def zip_of(version: str) -> Path:
        folder = work / f"zip-{version}"
        (folder / "wheelhouse").mkdir(parents=True)
        source = pack._stamped_copy(version, work / f"src-{version}")
        subprocess.run(  # noqa: S603
            ["uv", "build", "-q", "--wheel", "-o", str(folder / "wheelhouse"), str(source)],  # noqa: S607
            check=True,
        )
        for wheel in wheels.glob("*.whl"):
            shutil.copy2(wheel, folder / "wheelhouse")
        for name in ("install.pyw", "launch.pyw"):
            shutil.copy2(repo / "ai-workspace-helper" / name, folder / name)
        shutil.copy2(repo / "ai-workspace-helper/src/ai_workspace_helper/setup_core.py", folder)
        return folder

    def run(argv: list[str]) -> None:
        if argv[0] == "powershell":  # the shortcuts: Windows only
            return
        setup_core.run_quietly(argv)

    print("\n=== update: install 0.1.0, then 0.2.0 over it (D21: install.pyw again)", flush=True)
    installed = setup_core.install(zip_of("0.1.0"), home, base_python=sys.executable, run=run)
    check(
        "0.1.0 installs from the wheelhouse alone (venv, pip, self-test)",
        installed.version == "0.1.0",
    )
    installed = setup_core.install(zip_of("0.2.0"), home, base_python=sys.executable, run=run)
    state = setup_core.read_state(home)
    check(
        "0.2.0 installs beside it: current, 0.1.0 kept as previous",
        (installed.version, state["current"], state["previous"]) == ("0.2.0", "0.2.0", "0.1.0"),
        json.dumps({k: state.get(k) for k in ("current", "previous")}),
    )

    started: list[list[str]] = []
    chosen = setup_core.launch(home, popen=lambda argv, cwd: started.append(argv))
    check("the next start takes 0.2.0", chosen == "0.2.0", chosen)
    probe = subprocess.run(  # noqa: S603 — that version's own interpreter, for real
        [started[0][0].replace("pythonw", "python"), "-m", "ai_workspace_helper", "--self-test"],
        capture_output=True,
        text=True,
    )
    check(
        "…and that version runs",
        probe.returncode == 0 and "ai-workspace-helper 0.2.0" in probe.stdout,
        probe.stdout.strip() or probe.stderr.strip()[-200:],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Live check of the helper")
    parser.add_argument(
        "--update-wheels",
        type=Path,
        help="a folder of THIS machine's wheels for playwright and its dependencies; "
        "with it, the update check runs too (pip download playwright==<the repo's>)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path(tempfile.mkdtemp(prefix="ai-workspace-helper-"))
    )
    parser.add_argument(
        "--release", type=Path, help="a pack_ai_workspace_helper.py output folder to publish"
    )
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parent.parent
    check = _Check()

    subprocess.run(  # noqa: S603 — the converter the helper ships, built from the frontend
        ["pnpm", "run", "build:ai-workspace-helper"],  # noqa: S607
        cwd=repo / "web",
        check=True,
        capture_output=True,
    )
    llm_port, app_port, site_port = _free_port(), _free_port(), _free_port()
    work = Path(tempfile.mkdtemp(prefix="ai-workspace-helper-live-"))
    release = args.release or work / "release"
    release.mkdir(exist_ok=True)
    for name, text in (("endpoint.py", _ENDPOINT), ("site.py", _SITE), ("helper.py", _HELPER)):
        (work / name).write_text(text, encoding="utf-8")
    (work / "config.yaml").write_text(
        _CONFIG.format(
            app_port=app_port, llm_port=llm_port, site_port=site_port, release_dir=release
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "WORKSPACE_APP_CONFIG": str(work / "config.yaml"),
        "WORKSPACE_TOOLS_DIR": os.environ.get("WORKSPACE_TOOLS_DIR", str(_tools_dir(repo))),
        "WORKSPACE_LLM_LOG": "0",
        "TOKEN": TOKEN,
        "QUERY": QUERY,
        "SITE": f"http://127.0.0.1:{site_port}",
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
        start([sys.executable, str(work / "site.py")], PORT=str(site_port))
        start([sys.executable, str(work / "endpoint.py")], PORT=str(llm_port))
        _wait(f"http://127.0.0.1:{llm_port}/v1/models")
        start([sys.executable, "-m", "workspace_app"])
        _wait(f"{base}/api/apps")
        start(
            [sys.executable, str(work / "helper.py")],
            HELPER_SRC=str(repo / "ai-workspace-helper" / "src"),
            AI_WORKSPACE_HELPER_HOME=str(work / "helper-home"),
            APP_URL=base,
        )
        print(f"\napp {base} · logs in {work}", flush=True)

        title = "小幫手 live check"
        _, item = _req("POST", f"{base}/api/a/rca/items", {"title": title})
        iid = item["resource_id"]
        video = _tour(base, iid, out, check)

        # What the person never saw: the files, and the jobs, as the server has them
        _, tree = _req("GET", f"{base}/api/a/rca/items/{iid}/tree")
        files = tree.get("files", []) if isinstance(tree, dict) else []
        lookups = sorted(f["path"].lstrip("/") for f in files if "lookups/" in f["path"])
        check("two lookups were saved to the workspace", len(lookups) == 2, ", ".join(lookups))
        saved = [
            _req("GET", f"{base}/api/a/rca/items/{iid}/files/{urllib.parse.quote(p)}")[1]
            for p in lookups
        ]
        text = "\n".join(
            s if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in saved
        )
        check("each says the helper fetched it", text.count("由小幫手抓取") == 2)
        check(
            "the search results' relative link came back absolute",
            f"http://127.0.0.1:{site_port}/article" in text,
        )
        check(f"the article's {TOKEN} is in the saved page", TOKEN in text)

        if video and shutil.which("ffmpeg"):
            mp4 = out / "ai-workspace-helper-demo.mp4"
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

    if args.update_wheels:
        _update_check(repo, args.update_wheels, check)

    print(f"\n{'FAILED' if check.failed else 'all ok'} ({check.failed} failed)")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())

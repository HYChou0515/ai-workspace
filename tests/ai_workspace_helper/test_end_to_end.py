"""P5's acceptance (docs/plan-outside-helper.md §4): the helper — its real
Chrome, its real loop — against the real backend served over HTTP and a local
fake site. The card hands over, the helper fetches in Chrome, the card is
answered. Integration: a real Chromium and a real server."""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import uvicorn
from ai_workspace_helper.backend import Backend
from ai_workspace_helper.chrome import Chrome
from ai_workspace_helper.errands import FetchedPage
from ai_workspace_helper.worker import Worker

from tests.api.test_outside_answers import _answers, _asked

pytestmark = pytest.mark.integration


class _Site(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<title>pandas 2.0 新功能</title><p>copy-on-write</p>".encode())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served():
    client, spec, iid = _asked()
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(client.app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    site = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    yield (
        client,
        spec,
        iid,
        f"http://127.0.0.1:{port}",
        f"http://127.0.0.1:{site.server_address[1]}",
    )
    site.shutdown()
    server.should_exit = True


def test_the_helper_answers_the_card_from_its_own_chrome(tmp_path, served):
    client, spec, iid, app_url, site = served

    def search(ctx, query):
        ctx.page.goto(f"{site}/search?q={query}")
        return FetchedPage(title=ctx.page.title(), url=ctx.page.url, markdown="copy-on-write")

    chrome = Chrome(tmp_path / "profile", app_url, channel="chromium", headless=True)
    try:
        worker = Worker(Backend(chrome.send), chrome, errands={"search": search})
        worker.tick()  # checks in
        # the keywords the person typed (plan-outside-lookup D13)
        r = client.post(
            f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": "c1", "query": "pandas 2.0"}
        )
        assert r.status_code == 201, r.text

        worker.tick()
    finally:
        chrome.close()

    assert worker.status == "connected", worker.detail
    [answer] = _answers(spec)
    assert "copy-on-write" in answer.content
    assert client.get(f"/a/rca/items/{iid}/outside-jobs/c1").json()["state"] == "done"

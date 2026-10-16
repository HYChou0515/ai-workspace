"""The external AI's site-file TEMPLATE in a real Chrome (docs/plan-ext-ai.md
§4): on a chat page it has never seen, it signs in (the person does), types the
question, sends it, waits for the answer to be written out — through a pause
with the page still saying it is writing — and brings back the answer alone,
as the Markdown a paste would have made.

Integration: a real Chromium, and the converter BUILT from the frontend source.
"""

from __future__ import annotations

import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from ai_workspace_helper.chrome import Chrome
from ai_workspace_helper.ext_ai import errand, load_site

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "ai-workspace-helper" / "ext_ai_site.template.py"

# Written the way the tool asks the AI to write: whole, and in Markdown — which
# the page renders, so the person's own bubble no longer reads as the raw
# question (review round 1).
# …and its numbered list is drawn without its numbers (review round 2)
QUESTION = "Which is **safer** for a 2 GB CSV:\n1. pandas\n2. polars\nOne line each."

# A chat page like the ones the template has to guess at: a sign-in first (a
# person signs in a moment later), then a blank conversation; Enter sends; the
# answer comes a few words at a time with a 「Stop generating」 button showing,
# and a pause longer than the template's quiet time in the middle of it.
_CHAT = """<!doctype html><meta charset="utf-8"><title>Company AI</title>
<header><nav>New chat · History · Settings</nav></header>
<div id="login"><p>Sign in to Company AI</p>
  <input type="text" name="username" size="40"><input type="password"></div>
<main id="chat" hidden>
  <textarea id="prompt" rows="3" cols="80" placeholder="Ask anything"></textarea></main>
<script>
const login = document.getElementById('login'), chat = document.getElementById('chat');
// signed in, the page draws the conversation a moment after it loads, as a
// single-page app does — not a sign-in to ask the person about
if (document.cookie.includes('signed=1')) {
  login.remove(); setTimeout(() => { chat.hidden = false; }, 1200);
}
else setTimeout(() => { document.cookie = 'signed=1'; location.reload(); }, 1500);
const words = 'Polars uses less memory for a file that size'.split(' ');
document.getElementById('prompt').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.shiftKey) return;
  e.preventDefault();
  const q = e.target.value; e.target.value = '';
  // a blank conversation has no message list until the first message: it
  // comes NEW, holding the question and the answer both
  let log = document.getElementById('log');
  if (!log) { log = document.createElement('div'); log.id = 'log'; chat.prepend(log); }
  // the page renders the question's Markdown, as chat pages do
  const me = document.createElement('div'); me.className = 'me';
  const html = q.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>').split('\\n')
    .map((l) => /^(- |\\d+\\. )/.test(l)
      ? `<li>${l.replace(/^(- |\\d+\\. )/, '')}</li>` : `<p>${l}</p>`).join('');
  me.innerHTML = html.replace(/(<li>.*<\\/li>)/, '<ol>$1</ol>'); log.append(me);
  const bot = document.createElement('div'); bot.className = 'bot'; log.append(bot);
  const p = document.createElement('p'); bot.append(p);
  const stop = document.createElement('button'); stop.innerText = 'Stop generating';
  document.body.append(stop);
  let i = 0;
  const next = () => {
    if (i < words.length) {
      p.append((i ? ' ' : '') + words[i++]);
      setTimeout(next, i === 4 ? 4000 : 120);   // a long think in the middle
      return;
    }
    bot.insertAdjacentHTML('beforeend',
      '<ul><li>lazy frames</li><li>Arrow memory</li></ul>' +
      '<p>See <a href="https://pola.example/guide">the guide</a>.</p>');
    stop.remove();
    const copy = document.createElement('button'); copy.innerText = 'Copy'; bot.after(copy);
  };
  setTimeout(next, 600);
});
</script>"""


@pytest.fixture(scope="module", autouse=True)
def bundle():
    subprocess.run(
        ["pnpm", "run", "build:ai-workspace-helper"],
        cwd=_ROOT / "web",
        check=True,
        capture_output=True,
    )


class _Site(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        body = _CHAT.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def chat_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/new"
    server.shutdown()


@pytest.fixture
def site(tmp_path, chat_url):
    f = tmp_path / "ext_ai_site.py"
    f.write_text(
        _TEMPLATE.read_text(encoding="utf-8").replace('URL = "https://"', f'URL = "{chat_url}"'),
        encoding="utf-8",
    )
    return load_site(f)


def test_the_template_asks_a_chat_page_it_has_never_seen(tmp_path, site):
    chrome = Chrome(tmp_path / "profile", "http://unused", channel="chromium", headless=True)
    notes: list[str] = []
    try:
        page = chrome.run(errand(site), QUESTION, notes.append)
    finally:
        chrome.close()

    md = page.markdown
    assert "Polars uses less memory for a file that size" in md  # all of it, past the pause
    assert re.search(r"^-\s+lazy frames$", md, re.M)
    assert "[the guide](https://pola.example/guide)" in md
    # the answer alone: not the question, the page's furniture, or its buttons
    assert "safer" not in md and "pandas" not in md
    assert "History" not in md and "Copy" not in md and "Stop" not in md
    # it waited for the person to sign in, and said so
    assert notes[0] == "login"
    assert (page.title, page.url) == ("外部AI", "")


def test_signed_in_it_asks_again_without_asking_anyone_to_sign_in(tmp_path, site):
    """A page that draws its conversation a moment after loading is not a
    sign-in: the second question, signed in, brings no window forward and
    tells the card nothing about signing in (review round 1)."""
    chrome = Chrome(tmp_path / "profile", "http://unused", channel="chromium", headless=True)
    first: list[str] = []
    again: list[str] = []
    try:
        chrome.run(errand(site), QUESTION, first.append)
        page = chrome.run(errand(site), QUESTION, again.append)
    finally:
        chrome.close()

    assert "login" in first and "login" not in again
    assert "Polars uses less memory" in page.markdown

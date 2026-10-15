"""The external AI's site file — a template (docs/plan-ext-ai.md D7, §6).

The helper asks the company's web AI by running ONE function from this file in
the helper's own Chrome. Nothing here is sent to AI Workspace's server; the
file is packed into the helper.

1. Copy this file to `ext_ai_site.py` and set `URL` below.
2. Try it on your own PC, against the real page (you sign in once in the
   window that opens; the question and the answer print in your terminal):

       python -m ai_workspace_helper try-ext-ai --site ext_ai_site.py "a question"

3. Where your page differs from what `ask` assumes, change `ask`. Each step is
   its own few lines: find the box, type, send, wait, take the answer.
4. Pack it into the helper:

       python3 scripts/pack_ai_workspace_helper.py --version <new> --ext-ai-site ext_ai_site.py

`ask(page, question, h)` gets a Playwright `page` (a fresh tab in the helper's
Chrome — https://playwright.dev/python/docs/api/class-page), the question, and
`h`. Wait through `h` — anything longer than a few seconds:

- `h.wait_for_login(ready)` — while `ready()` is false (a sign-in page),
  brings the window forward and waits for the person;
- `h.wait_until(cond, what)` — waits until `cond()` holds;
- `h.wait_still(seconds)` — waits until the page's text stops changing.

They tell the card the helper is still at it while they wait — waited
without them for 120 s, the card falls back to asking by hand, though the
helper is still asking — and they stop at the helper's
limit, saying what they waited for. A bare `page.wait_for_timeout(…)` or a
long `wait_for_selector` does neither; a short one — like the ten seconds
`ask` below gives a page to draw itself — is fine.

Return the answer element's HTML (`locator.evaluate("e => e.outerHTML")`), or
its text. The helper turns an element's HTML into Markdown the way a paste into
the card would; text it keeps as it is.
"""

#: The page that opens on a blank conversation.
URL = "https://"

#: What the helper reports when it checks in — a label, shown to nobody yet.
NAME = "external AI"

#: Where a question can be typed. Not a one-line `input`: a sign-in page has
#: those (its user name), and the question would go to the sign-in.
PROMPT = "textarea, [contenteditable='true'], [contenteditable='']"

#: A sign-in in the way: a visible password field — told apart from a page
#: still drawing itself.
SIGN_IN = "input[type='password']"

#: How long a page that draws itself after loading gets to show either.
FIRST_DRAW_SECONDS = 10

#: How long the answer must stay unchanged to count as written out.
STILL_SECONDS = 3.0

# Everything on the page before the question goes out is marked; what is not
# marked afterwards came with the question and its answer.
_MARK = """() => {
  for (const el of document.body.querySelectorAll('*')) el.setAttribute('data-aiwh-old', '');
}"""

# The answer: of what came after the question, the element with the most text
# that does not hold the question itself (the person's own bubble, and any
# list wrapping both, hold it). Each conversation starts blank, so there is
# little else new. `null` until something has come.
_ANSWER = """(question) => {
  // A page shows the question RENDERED — its Markdown drawn, links shown by
  // their text, a numbered list without its numbers, spaces folded — so the
  // two are compared by their letters alone.
  const bare = (s) => s.replace(/\\]\\([^)]*\\)/g, ']').replace(/[^\\p{L}]+/gu, '');
  const q = bare(question);
  let best = null, size = 0;
  for (const el of document.body.querySelectorAll('*:not([data-aiwh-old])')) {
    if (el.closest('textarea, [contenteditable], input, button, script, style')) continue;
    const text = bare(el.innerText || '');
    if (!text || (q && text.includes(q))) continue;
    if (text.length > size) { best = el; size = text.length; }
  }
  return best ? best.outerHTML : null;
}"""

# Still writing: a visible button saying so — "Stop", "停止" — as most chat
# pages show while an answer is on its way.
_BUSY = """() => [...document.querySelectorAll('button, [role=button]')].some((b) => {
  const name = `${b.getAttribute('aria-label') || ''} ${b.innerText || ''}`;
  return b.offsetParent !== null && /stop|停止|中止/i.test(name);
})"""


def _prompt_box(page):
    """The largest visible place to type, or None (a sign-in page has none)."""
    boxes = page.locator(PROMPT)
    best, area = None, 0.0
    for i in range(boxes.count()):
        box = boxes.nth(i)
        if not box.is_visible():
            continue
        rect = box.bounding_box() or {"width": 0, "height": 0}
        if rect["width"] * rect["height"] > area:
            best, area = box, rect["width"] * rect["height"]
    return best


def _signed_in(page):
    """The blank conversation is up: there is a place to type a question."""
    return _prompt_box(page) is not None


def ask(page, question, h):
    page.goto(URL)
    # a page that draws itself after loading shows the conversation or the
    # sign-in a moment later — not a sign-in to ask the person about
    for _ in range(FIRST_DRAW_SECONDS):
        if _signed_in(page) or page.locator(SIGN_IN).first.is_visible():
            break
        page.wait_for_timeout(1000)
    h.wait_for_login(lambda: _signed_in(page))
    box = _prompt_box(page)
    page.evaluate(_MARK)

    # type the question: a text box takes it at once; a rich editor is typed into
    box.click()
    if box.evaluate("el => el.isContentEditable"):
        page.keyboard.insert_text(question)
    else:
        box.fill(question)
    # send it — if your page sends on a button instead, click that button here
    page.keyboard.press("Enter")

    h.wait_until(lambda: page.evaluate(_ANSWER, question) is not None, "外部AI 開始回答")
    h.wait_still(STILL_SECONDS)
    h.wait_until(lambda: not page.evaluate(_BUSY), "外部AI 把回答寫完")
    return page.evaluate(_ANSWER, question)

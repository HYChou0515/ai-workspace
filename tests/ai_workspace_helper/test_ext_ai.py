"""Asking the company's web AI (docs/plan-ext-ai.md §4).

The company writes one file — `URL` and `ask(page, question, h)` — packed into
the helper; the helper loads it, runs it as the `ask_ext_ai` errand, and gives
it `h`: waits that keep the job held (a claim lapses in 120 s) and end at a
limit, so a site file that waits too long fails the job instead of having it
asked twice.
"""

from __future__ import annotations

import types

import pytest
from ai_workspace_helper.errands import RENEW_S, ErrandContext, ErrandFailed


class _Page:
    def __init__(self, texts=None) -> None:
        self.fronted = 0
        self._texts = iter(texts or [])

    def bring_to_front(self) -> None:
        self.fronted += 1

    def evaluate(self, _script):  # noqa: ANN001, ANN201 — the page's text, as it changes
        return next(self._texts)


def _ctx(page, notes, clock, converted=None):  # noqa: ANN001, ANN202
    return ErrandContext(
        page=page,
        note=notes.append,
        convert=lambda html: (
            (converted.append(html) if converted is not None else None) or f"MD({html})"
        ),
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        clock=lambda: clock[0],
    )


def _site(answer, *, seen=None):  # noqa: ANN001, ANN202
    def ask(page, question, h):  # noqa: ANN001, ANN202
        if seen is not None:
            seen.append((page, question, h))
        return answer

    return types.SimpleNamespace(URL="https://ai.example/new", NAME="公司 AI", ask=ask)


# ── the errand ──────────────────────────────────────────────────────────────


def test_the_site_file_is_asked_on_the_errands_page_and_its_html_comes_back_as_markdown():
    from ai_workspace_helper.ext_ai import errand

    page, notes, clock, seen = _Page(), [], [0.0], []
    got = errand(_site("<div><p>Polars.</p></div>", seen=seen))(
        _ctx(page, notes, clock), "pandas or polars?"
    )

    [(on, question, _h)] = seen
    assert on is page and question == "pandas or polars?"
    assert got.markdown == "MD(<div><p>Polars.</p></div>)"
    # D12: the company's address never goes back to the server
    assert (got.title, got.url) == ("外部AI", "")


def test_an_answer_in_plain_text_is_kept_as_it_is():
    from ai_workspace_helper.ext_ai import errand

    got = errand(_site("Polars, for memory.\n\n- a < b"))(_ctx(_Page(), [], [0.0]), "q")

    assert got.markdown == "Polars, for memory.\n\n- a < b"


@pytest.mark.parametrize("answer", ["", "   ", None, 3])
def test_a_site_file_that_brings_back_nothing_fails_the_job(answer):
    from ai_workspace_helper.ext_ai import errand

    with pytest.raises(ErrandFailed, match="沒有交回回答"):
        errand(_site(answer))(_ctx(_Page(), [], [0.0]), "q")


def test_the_errand_is_there_only_with_a_site_file():
    from ai_workspace_helper.builtin_errands import build

    assert "ask_ext_ai" not in build(lambda: [])
    assert "ask_ext_ai" in build(lambda: [], site=_site("x"))


# ── h: the waits (D11) ──────────────────────────────────────────────────────


def _h(page, notes, clock, **kw):  # noqa: ANN001, ANN202
    from ai_workspace_helper.ext_ai import Helpers

    return Helpers(_ctx(page, notes, clock), **kw)


def test_wait_until_returns_once_it_holds():
    clock = [0.0]
    answers = iter([False, False, True])

    _h(_Page(), [], clock).wait_until(lambda: next(answers), "the answer to start")

    assert clock[0] > 0


def test_a_long_wait_keeps_the_job_held():
    """A claim lasts 120 s: the card is told again every RENEW_S, which renews it."""
    clock = [0.0]
    renewed: list[float] = []
    answers = iter([False] * 200 + [True])
    from ai_workspace_helper.ext_ai import Helpers

    ctx = _ctx(_Page(), [], clock)
    ctx.note = lambda _text: renewed.append(clock[0])
    Helpers(ctx).wait_until(lambda: next(answers), "x")

    # never longer than RENEW_S (+ the one-second poll) without telling the card
    gaps = [b - a for a, b in zip([0.0, *renewed], [*renewed, clock[0]], strict=True)]
    assert len(renewed) >= 2 and max(gaps) <= RENEW_S + 1
    assert RENEW_S < 120


def test_a_wait_past_the_limit_fails_the_job_saying_what_it_waited_for():
    clock = [0.0]

    with pytest.raises(ErrandFailed, match="the answer to start"):
        _h(_Page(), [], clock, timeout_s=30).wait_until(lambda: False, "the answer to start")

    assert 30 <= clock[0] < 32


def test_the_limit_is_for_the_whole_errand_not_each_wait():
    clock = [0.0]
    h = _h(_Page(), [], clock, timeout_s=30)
    answers = iter([False] * 20 + [True])
    h.wait_until(lambda: next(answers), "first")

    with pytest.raises(ErrandFailed):
        h.wait_until(lambda: False, "second")

    assert clock[0] < 32


def test_wait_still_returns_once_the_page_stops_changing():
    clock = [0.0]
    page = _Page(["a", "ab", "abc"] + ["abcd"] * 50)

    _h(page, [], clock).wait_still(seconds=3)

    # it saw "abcd" hold for three seconds
    assert 3 <= clock[0] < 10


def test_wait_still_fails_at_the_limit_on_a_page_that_never_stops():
    clock = [0.0]
    page = _Page([str(n) for n in range(1000)])

    with pytest.raises(ErrandFailed, match="寫完"):
        _h(page, [], clock, timeout_s=20).wait_still(seconds=3)


# ── h: signing in (D4) ──────────────────────────────────────────────────────


def test_a_page_already_signed_in_does_not_wait():
    page, notes, clock = _Page(), [], [0.0]

    _h(page, notes, clock).wait_for_login(lambda: True)

    assert (page.fronted, notes, clock) == (0, [], [0.0])


def test_a_sign_in_page_brings_the_window_forward_and_tells_the_card():
    page, notes, clock = _Page(), [], [0.0]
    answers = iter([False, False, True])

    _h(page, notes, clock).wait_for_login(lambda: next(answers))

    assert page.fronted == 1
    assert notes[0] == "login" and notes[-1] == ""


def test_time_spent_signing_in_does_not_count_against_the_answer():
    clock = [0.0]
    h = _h(_Page(), [], clock, timeout_s=30)
    signing_in = iter([False] * 25 + [True])
    h.wait_for_login(lambda: next(signing_in))
    answering = iter([False] * 20 + [True])

    h.wait_until(lambda: next(answering), "the answer")  # does not raise


def test_nobody_signing_in_fails_the_job():
    with pytest.raises(ErrandFailed, match="登入"):
        _h(_Page(), [], [0.0]).wait_for_login(lambda: False)


# ── loading the site file (D7) ──────────────────────────────────────────────


def test_a_site_file_is_loaded_from_a_path(tmp_path):
    from ai_workspace_helper.ext_ai import load_site

    f = tmp_path / "ext_ai_site.py"
    f.write_text('URL = "https://ai.example/new"\ndef ask(page, question, h):\n    return "x"\n')

    site = load_site(f)

    assert site is not None and site.URL == "https://ai.example/new"


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ('def ask(page, question, h):\n    return "x"\n', "URL"),
        ('URL = "https://ai.example"\n', "ask"),
        ('URL = ""\ndef ask(page, question, h):\n    return "x"\n', "URL"),
    ],
)
def test_a_site_file_missing_a_part_says_which(tmp_path, body, says):
    from ai_workspace_helper.ext_ai import load_site

    f = tmp_path / "ext_ai_site.py"
    f.write_text(body)

    with pytest.raises(ValueError, match=says):
        load_site(f)


def test_a_helper_packed_without_one_has_none():
    from ai_workspace_helper.ext_ai import load_site

    assert load_site() is None


def test_the_label_the_helper_reports_is_the_files_name_or_a_default():
    from ai_workspace_helper.ext_ai import site_label

    assert site_label(_site("x")) == "公司 AI"
    assert site_label(types.SimpleNamespace(URL="u", ask=None)) == "ext_ai_site"
    assert site_label(None) == ""


# ── try-ext-ai: the company tries its file against the real page (P4) ──────


class _TryChrome:
    """What `try-ext-ai` drives: `Chrome.run` and `close`."""

    def __init__(self) -> None:
        self.closed = False

    def run(self, errand, args, note, where=None):  # noqa: ANN001, ANN201
        page, clock = _Page(), [0.0]
        ctx = ErrandContext(
            page=page,
            note=note,
            convert=lambda html: f"MD({html})",
            sleep=lambda s: clock.__setitem__(0, clock[0] + s),
            clock=lambda: clock[0],
        )
        return errand(ctx, args)

    def close(self) -> None:
        self.closed = True


def _site_file(tmp_path, body: str):  # noqa: ANN001, ANN202
    f = tmp_path / "ext_ai_site.py"
    f.write_text('URL = "https://ai.example/new"\n' + body)
    return f


def test_try_ext_ai_prints_the_question_and_the_answer(tmp_path):
    from ai_workspace_helper.ext_ai import try_main

    f = _site_file(
        tmp_path,
        "def ask(page, question, h):\n"
        "    signed = iter([False, True])\n"
        "    h.wait_for_login(lambda: next(signed))\n"
        "    return '<p>' + question.upper() + '</p>'\n",
    )
    out: list[str] = []
    chrome = _TryChrome()

    code = try_main(["--site", str(f), "pandas?"], make_chrome=lambda: chrome, out=out.append)

    assert code == 0 and chrome.closed
    text = "\n".join(out)
    assert "pandas?" in text  # what was asked
    assert "登入" in text  # where to sign in, while it waited
    assert "MD(<p>PANDAS?</p>)" in text  # the answer, as the card would get it


def test_try_ext_ai_says_why_it_failed(tmp_path):
    from ai_workspace_helper.ext_ai import try_main

    f = _site_file(
        tmp_path,
        "def ask(page, question, h):\n    h.wait_until(lambda: False, '外部AI 開始回答')\n",
    )
    out: list[str] = []
    chrome = _TryChrome()

    code = try_main(["--site", str(f), "q"], make_chrome=lambda: chrome, out=out.append)

    assert code == 1 and chrome.closed
    assert "外部AI 開始回答" in "\n".join(out)


def test_try_ext_ai_refuses_a_broken_file_before_opening_chrome(tmp_path):
    from ai_workspace_helper.ext_ai import try_main

    f = _site_file(tmp_path, "")  # no ask
    opened: list[object] = []
    out: list[str] = []

    code = try_main(["--site", str(f), "q"], make_chrome=lambda: opened.append(1), out=out.append)

    assert code == 2 and opened == []
    assert "ask" in "\n".join(out)


# ── the whole way: card → helper → card, against the real backend ──────────
#
# The backend's job kind and the helper's errand must be the SAME name, and
# the helper must say it can — each side's own tests would stay green with
# the two apart.


def test_a_question_handed_over_on_the_card_is_asked_and_answers_the_card():
    from ai_workspace_helper.builtin_errands import build
    from ai_workspace_helper.ext_ai import site_label
    from ai_workspace_helper.worker import Worker

    from tests.ai_workspace_helper.test_worker import _backend
    from tests.api.test_ext_ai_jobs import QUESTION, _asked
    from tests.api.test_outside_answers import _answers

    client, spec, iid = _asked()
    asked: list[str] = []

    def ask(page, question, h):  # noqa: ANN001, ANN202
        asked.append(question)
        return "<p>Polars, for memory.</p>"

    site = types.SimpleNamespace(URL="https://ai.example/new", NAME="公司 AI", ask=ask)
    worker = Worker(
        _backend(client),
        _TryChrome(),
        errands=build(lambda: [], site=site),
        info=lambda: {"ext_ai": site_label(site)},
        date=lambda: "2031-01-02",
    )
    worker.tick()  # checks in, saying it can ask the external AI
    r = client.post(
        f"/a/rca/items/{iid}/outside-jobs", json={"tool_call_id": "c1", "query": QUESTION}
    )
    assert r.status_code == 201, r.text

    worker.tick()

    assert asked == [QUESTION]
    [answer] = _answers(spec)
    assert answer.content.startswith("我問了外部AI:")
    assert "MD(<p>Polars, for memory.</p>)" in answer.content

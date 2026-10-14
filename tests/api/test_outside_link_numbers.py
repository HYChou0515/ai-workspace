"""The links a lookup brings back are listed, numbered, at the bottom of its
file (docs/plan-outside-helper.md D24).

The AI copied long links wrong — a search's results are Google redirect links
(`https://www.google.com/url?q=…&sa=U&ved=…`) — and a link copied wrong is a
page nobody asked for and one the server never saw come in (D12), so it waits
for the person's 「查」 (the person: 「他對於摳網址有障礙 通常會摳錯」). So the
links are taken out and listed under the answer, numbered (the person: 「自動把
網址摳出來放在底下配編號 ai使用時需要指定檔名+編號」), and the AI names a
file and a number. The answer itself is left as it came: rewriting its links
in place broke a link with parentheses, collided with the reference links a
pasted page already had, and rewrote links inside code (review of #941).
"""

from __future__ import annotations

import pytest

from tests.api.test_outside_answers import _answer, _answers, _as_carol, _asked, _read
from workspace_app.agent.outside_lookup import LINK_LIST_MARKER, listed_links
from workspace_app.api.outside_jobs import link_list, links_in, numbered_links

# ── what counts as a link, and which page it is ──


def test_inline_links_and_autolinks_in_order_each_page_once():
    md = (
        "see [whatsnew](https://pandas.example/whatsnew) and <https://b.example/x>, "
        "[again](https://pandas.example/whatsnew#top)"
    )

    assert numbered_links(md) == [
        (1, "whatsnew", "https://pandas.example/whatsnew"),
        (2, "", "https://b.example/x"),
    ]


# The pasted page's converter escapes parentheses in an address (turndown).
def test_a_link_with_parentheses_is_the_whole_address():
    md = r"[Foo](https://en.wikipedia.org/wiki/Foo_\(bar\)) [Baz](https://w.example/a_(b)_c)"

    assert [u for _, _, u in numbered_links(md)] == [
        "https://en.wikipedia.org/wiki/Foo_(bar)",
        "https://w.example/a_(b)_c",
    ]


def test_a_titled_link_is_a_link():
    md = '[Pandas](https://pandas.example/ "Pandas — Wikipedia")'

    assert numbered_links(md) == [(1, "Pandas", "https://pandas.example/")]


def test_link_text_may_hold_brackets_and_images():
    md = (
        "[see [1] here](https://a.example/) "
        "[![logo](https://img.example/l.png)](https://b.example/)"
    )

    assert [(t, u) for _, t, u in numbered_links(md)] == [
        ("see [1] here", "https://a.example/"),
        ("![logo](https://img.example/l.png)", "https://b.example/"),
    ]


def test_links_in_code_are_not_links():
    md = (
        "`[a](https://code.example/a)` and\n"
        "```\n<https://code.example/b>\n[c](https://code.example/c)\n```\n"
        "[real](https://real.example/)"
    )

    assert [u for _, _, u in numbered_links(md)] == ["https://real.example/"]


def test_only_web_pages_are_listed():
    md = "[mail](mailto:a@b.example) [js](javascript:x) [rel](/docs/x) [ok](http://ok.example/)"

    assert [u for _, _, u in numbered_links(md)] == ["http://ok.example/"]


GOOGLE = "https://www.google.com/url?q=https://pandas.example/whatsnew&sa=U&ved=2ah"


@pytest.mark.parametrize(
    ("link", "page"),
    [
        (GOOGLE, "https://pandas.example/whatsnew"),
        (
            "https://www.google.com.tw/url?sa=t&url=https%3A%2F%2Fa.example%2Fp%3Fx%3D1&ved=0",
            "https://a.example/p?x=1",
        ),
        ("https://google.com/url?q=http://b.example/", "http://b.example/"),
    ],
)
def test_a_google_redirect_link_is_listed_as_the_page_it_leads_to(link, page):
    assert numbered_links(f"[r]({link})") == [(1, "r", page)]


@pytest.mark.parametrize(
    "link",
    [
        "https://www.google.com/search?q=https://not-a-redirect.example/",
        "https://www.google.com/url?q=javascript:x",
        "https://notgoogle.example/url?q=https://a.example/",
        "https://www.google.com.evil.example/url?q=https://a.example/",
        "https://www.google.com/url?sa=U",
    ],
)
def test_anything_else_is_listed_as_it_is(link):
    assert numbered_links(f"[r]({link})") == [(1, "r", link)]


# D12 as it was: what came back is remembered as it came — the redirect too, so
# a card for it still opens with no nod — and now also the page it leads to.
def test_the_chat_remembers_a_redirect_and_the_page_it_leads_to():
    assert links_in(f"[r]({GOOGLE})") == [GOOGLE, "https://pandas.example/whatsnew"]


def test_a_page_linked_twice_is_remembered_once():
    md = "[a](https://a.example/p) [b](https://a.example/p#part) <https://a.example/p>"

    assert links_in(md) == ["https://a.example/p"]


# ── the list under the answer, and what the AI reads back from it ──


def test_the_list_goes_under_the_answer_and_reads_back_by_number():
    md = "[whatsnew](https://pandas.example/whatsnew) <https://b.example/x>"

    listed = link_list(numbered_links(md))

    assert listed.startswith(f"\n\n{LINK_LIST_MARKER}\n")
    assert "- [1] <https://pandas.example/whatsnew> whatsnew" in listed
    assert listed_links(md + listed) == {
        1: "https://pandas.example/whatsnew",
        2: "https://b.example/x",
    }
    assert link_list([]) == ""


# The answer's own `[1]: …` reference links (a pasted README), or a line that
# looks like the list, are not the list: only what follows the LAST marker is.
def test_the_answers_own_numbered_lines_are_not_the_list():
    md = (
        "Read [x][1] and [y](https://b.example/y).\n\n"
        f"[1]: https://a.example/x\n{LINK_LIST_MARKER}\n- [9] <https://evil.example/>"
    )

    text = md + link_list(numbered_links(md))

    # evil.example is a link the answer carries, so the list numbers it too —
    # as [2], the list's own number, never the [9] the answer wrote
    assert listed_links(text) == {1: "https://b.example/y", 2: "https://evil.example/"}


# The server's memory of what came in (D12) holds every page the list lists.
def test_the_listed_links_are_links_the_chat_remembers():
    md = f"[r]({GOOGLE}) <https://b.example/#top>"

    text = md + link_list(numbered_links(md))

    assert set(listed_links(text).values()) <= set(links_in(text))


# ── the saved answer and the message the AI reads ──

PAGE = f"results\n- [whatsnew]({GOOGLE})\n" + r"- [Foo](https://en.wikipedia.org/wiki/Foo_\(bar\))"


def test_the_saved_file_keeps_the_answer_and_lists_its_links_under_it():
    client, spec, iid = _asked()

    r = _answer(client, iid, content=PAGE)

    assert r.status_code == 200, r.text
    saved = _read(client, iid, r.json()["path"])
    assert PAGE in saved  # the answer as it came
    assert listed_links(saved) == {
        1: "https://pandas.example/whatsnew",
        2: "https://en.wikipedia.org/wiki/Foo_(bar)",
    }


def test_the_message_lists_the_numbers_and_says_how_to_open_one():
    client, spec, iid = _asked()

    path = _answer(client, iid, content=PAGE).json()["path"]

    [message] = _answers(spec)
    assert f"ask_outside(file=`{path}`, link=" in message.content
    assert "[1] whatsnew — pandas.example" in message.content
    assert "[2] Foo — en.wikipedia.org" in message.content


# The list in the message comes before the excerpt, so a long answer cut at
# the excerpt's end still shows every number.
def test_a_long_answer_still_shows_its_numbers():
    client, spec, iid = _asked()

    _answer(client, iid, content=PAGE + "\n\n" + "x" * 10_000)

    [message] = _answers(spec)
    assert "[2] Foo — en.wikipedia.org" in message.content


def test_with_nothing_saved_there_is_no_file_to_name():
    client, spec, iid = _as_carol(converse=["user:carol"])  # may chat, not add files

    r = _answer(client, iid, content=PAGE)

    assert r.json()["path"] is None
    [message] = _answers(spec)
    assert "ask_outside(file=" not in message.content


def test_a_page_of_many_links_lists_the_first_ones_and_says_the_file_has_the_rest():
    client, spec, iid = _asked()
    many = "\n".join(f"- [r{i}](https://r.example/{i})" for i in range(1, 61))

    _answer(client, iid, content=many)

    [message] = _answers(spec)
    assert "[50] r50 — r.example" in message.content
    assert "[51]" not in message.content
    assert "…還有 10 個,列在檔案最下面" in message.content

"""The links a lookup brings back are numbered in its file
(docs/plan-outside-helper.md D24).

The AI copied long links wrong — a search's results are Google redirect links
(`https://www.google.com/url?q=…&sa=U&ved=…`) — and a link copied wrong is a
page nobody asked for and one the server never saw come in (D12), so it waits
for the person's 「查」 (the person: 「他對於摳網址有障礙 通常會摳錯」). So the
answer's links become `[text][n]`, listed as `[n]: url` at the bottom of the
saved file, and the AI names a file and a number. Done where the answer is
saved — the one door a pasted answer and the helper's both go through.
"""

from __future__ import annotations

import pytest

from tests.api.test_outside_answers import _answer, _answers, _as_carol, _asked, _read
from workspace_app.api.outside_jobs import links_in, number_links

# ── numbering ──


def test_links_become_numbers_listed_at_the_bottom():
    md = "see [whatsnew](https://pandas.example/whatsnew) and <https://b.example/x>"

    assert number_links(md) == (
        "see [whatsnew][1] and [2]\n\n[1]: https://pandas.example/whatsnew\n[2]: https://b.example/x"
    )


def test_the_same_link_twice_is_one_number():
    md = "[a](https://a.example/) [again](https://a.example/) ![pic](https://img.example/p.png)"

    assert number_links(md) == (
        "[a][1] [again][1] ![pic][2]\n\n[1]: https://a.example/\n[2]: https://img.example/p.png"
    )


def test_text_without_links_is_left_alone():
    assert number_links("nothing to open here") == "nothing to open here"


@pytest.mark.parametrize(
    ("link", "page"),
    [
        (
            "https://www.google.com/url?q=https://pandas.example/whatsnew&sa=U&ved=2ah",
            "https://pandas.example/whatsnew",
        ),
        (
            "https://www.google.com.tw/url?sa=t&url=https%3A%2F%2Fa.example%2Fp%3Fx%3D1&ved=0",
            "https://a.example/p?x=1",
        ),
        ("https://google.com/url?q=http://b.example/", "http://b.example/"),
    ],
)
def test_a_google_redirect_link_is_listed_as_the_page_it_leads_to(link, page):
    assert number_links(f"[r]({link})") == f"[r][1]\n\n[1]: {page}"
    assert links_in(f"[r]({link})") == [page]


@pytest.mark.parametrize(
    "link",
    [
        "https://www.google.com/search?q=https://not-a-redirect.example/",
        "https://www.google.com/url?q=javascript:x",
        "https://notgoogle.example/url?q=https://a.example/",
        "https://www.google.com/url?sa=U",
    ],
)
def test_anything_else_is_listed_as_it_is(link):
    assert number_links(f"[r]({link})") == f"[r][1]\n\n[1]: {link}"


# The server's memory of what came in (D12) holds exactly what the file lists:
# a link named by its number is one the chat has seen.
def test_the_listed_links_are_the_links_the_chat_remembers():
    md = "[r](https://www.google.com/url?q=https://a.example/p&sa=U) <https://b.example/#top>"

    numbered = number_links(md)

    assert links_in(numbered) == links_in(md) == ["https://a.example/p", "https://b.example/"]


# ── the saved answer and the message the AI reads ──

PAGE = (
    "results\n"
    "- [whatsnew](https://www.google.com/url?q=https://pandas.example/whatsnew&sa=U&ved=2ah)\n"
    "- [api](https://pandas.example/api)"
)


def test_the_saved_file_numbers_the_links_and_lists_them_at_the_bottom():
    client, spec, iid = _asked()

    r = _answer(client, iid, content=PAGE)

    assert r.status_code == 200, r.text
    saved = _read(client, iid, r.json()["path"])
    assert "- [whatsnew][1]\n- [api][2]" in saved
    assert saved.rstrip().endswith(
        "[1]: https://pandas.example/whatsnew\n[2]: https://pandas.example/api"
    )
    assert "google.com/url" not in saved


def test_the_message_shows_the_numbers_and_how_to_open_one():
    client, spec, iid = _asked()

    path = _answer(client, iid, content=PAGE).json()["path"]

    [message] = _answers(spec)
    assert "- [whatsnew][1]" in message.content
    assert f"ask_outside(file=`{path}`, link=" in message.content


def test_with_nothing_saved_there_is_no_file_to_name():
    client, spec, iid = _as_carol(converse=["user:carol"])  # may chat, not add files

    r = _answer(client, iid, content=PAGE)

    assert r.json()["path"] is None
    [message] = _answers(spec)
    assert "ask_outside(file=" not in message.content

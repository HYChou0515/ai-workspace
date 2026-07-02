"""MaintainerWikiStore (#377) — the wiki maintainer/unfolder agent's guarded
view of the wiki. It may edit every page EXCEPT the reserved clarification page,
where human answers live; writes/deletes to that page are silently dropped so a
rebuild can't clobber them, while reads stay open."""

from __future__ import annotations

from workspace_app.kb.wiki.store import CLARIFICATIONS_PATH, MaintainerWikiStore, WikiFileStore
from workspace_app.resources import make_spec


async def test_guard_ignores_agent_writes_to_the_reserved_page():
    spec = make_spec(default_user="u")
    inner = WikiFileStore(spec)
    guarded = MaintainerWikiStore(inner)
    await inner.write("c", CLARIFICATIONS_PATH, b"human answers")  # authored via raw store
    await guarded.write("c", CLARIFICATIONS_PATH, b"AGENT CLOBBER")  # agent try → ignored
    assert (await inner.read("c", CLARIFICATIONS_PATH)) == b"human answers"


async def test_guard_allows_agent_writes_to_normal_pages():
    spec = make_spec(default_user="u")
    guarded = MaintainerWikiStore(WikiFileStore(spec))
    await guarded.write("c", "/index.md", b"hello")
    assert (await guarded.read("c", "/index.md")) == b"hello"


async def test_guard_ignores_agent_deletes_of_the_reserved_page():
    spec = make_spec(default_user="u")
    inner = WikiFileStore(spec)
    guarded = MaintainerWikiStore(inner)
    await inner.write("c", CLARIFICATIONS_PATH, b"answers")
    await guarded.delete("c", CLARIFICATIONS_PATH)  # ignored
    assert await inner.exists("c", CLARIFICATIONS_PATH)


async def test_guard_matches_the_reserved_path_regardless_of_leading_slash():
    spec = make_spec(default_user="u")
    inner = WikiFileStore(spec)
    guarded = MaintainerWikiStore(inner)
    await inner.write("c", CLARIFICATIONS_PATH, b"answers")
    await guarded.write("c", "clarifications.md", b"clobber")  # no leading slash
    await guarded.write("c", "./clarifications.md", b"clobber2")  # dot-relative
    assert (await inner.read("c", CLARIFICATIONS_PATH)) == b"answers"


async def test_guard_reports_a_cas_write_to_the_reserved_page_as_lost():
    spec = make_spec(default_user="u")
    guarded = MaintainerWikiStore(WikiFileStore(spec))
    assert await guarded.write_cas("c", CLARIFICATIONS_PATH, b"x", None) is False


async def test_guard_allows_a_cas_write_to_a_normal_page():
    spec = make_spec(default_user="u")
    guarded = MaintainerWikiStore(WikiFileStore(spec))
    assert await guarded.write_cas("c", "/index.md", b"v1", None) is True
    assert (await guarded.read("c", "/index.md")) == b"v1"


async def test_guard_allows_deleting_a_normal_page():
    spec = make_spec(default_user="u")
    guarded = MaintainerWikiStore(WikiFileStore(spec))
    await guarded.write("c", "/tmp.md", b"x")
    await guarded.delete("c", "/tmp.md")
    assert not await guarded.exists("c", "/tmp.md")


async def test_guard_ignores_agent_write_from_path_to_the_reserved_page(tmp_path):
    spec = make_spec(default_user="u")
    inner = WikiFileStore(spec)
    guarded = MaintainerWikiStore(inner)
    await inner.write("c", CLARIFICATIONS_PATH, b"human answers")
    src = tmp_path / "src.md"
    src.write_bytes(b"AGENT CLOBBER")
    await guarded.write_from_path("c", CLARIFICATIONS_PATH, src)  # ignored
    assert (await inner.read("c", CLARIFICATIONS_PATH)) == b"human answers"


async def test_guard_allows_agent_write_from_path_to_a_normal_page(tmp_path):
    spec = make_spec(default_user="u")
    guarded = MaintainerWikiStore(WikiFileStore(spec))
    src = tmp_path / "src.md"
    src.write_bytes(b"from file")
    await guarded.write_from_path("c", "/notes.md", src)
    assert (await guarded.read("c", "/notes.md")) == b"from file"

"""WikiFileStore optimistic concurrency (#50 cross-worker hardening).

specstar v0.11.6's etag bumps on in-place draft modify(), so a stale write is
rejected — which is what lets two ingest workers edit one collection's wiki
without one silently clobbering the other.
"""

from __future__ import annotations

from workspace_app.kb.wiki.store import WikiFileStore
from workspace_app.resources import Collection, make_spec


async def _store_and_cid():
    spec = make_spec(default_user="u")
    cid = spec.get_resource_manager(Collection).create(Collection(name="c")).resource_id
    return WikiFileStore(spec), cid


async def test_write_cas_rejects_a_stale_etag_and_accepts_the_current_one():
    store, cid = await _store_and_cid()
    await store.write(cid, "/p.md", b"v1")

    got = await store.read_with_etag(cid, "/p.md")
    assert got is not None
    data, stale_etag = got
    assert data == b"v1"

    # A concurrent writer moves the page on (etag bumps even for in-place modify).
    await store.write(cid, "/p.md", b"v2")

    # Our write with the stale token is refused...
    assert await store.write_cas(cid, "/p.md", b"v1-edit", stale_etag) is False
    assert await store.read(cid, "/p.md") == b"v2"  # unchanged

    # ...and accepted with the current token.
    _, fresh = await store.read_with_etag(cid, "/p.md")  # type: ignore[misc]
    assert await store.write_cas(cid, "/p.md", b"v3", fresh) is True
    assert await store.read(cid, "/p.md") == b"v3"


async def test_write_cas_with_none_etag_is_create_if_not_exists():
    store, cid = await _store_and_cid()
    # None etag = "must not exist yet" → creates.
    assert await store.write_cas(cid, "/new.md", b"hello", None) is True
    # A second create loses the race (page already there).
    assert await store.write_cas(cid, "/new.md", b"other", None) is False
    assert await store.read(cid, "/new.md") == b"hello"


async def test_read_with_etag_is_none_for_a_missing_page():
    store, cid = await _store_and_cid()
    assert await store.read_with_etag(cid, "/nope.md") is None

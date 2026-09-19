"""`GET /files/{path}` honours a single byte `Range` (plan-chat-video-export
review, defect 11): a `<video>` over the file route could not seek, and
Safari refuses media the server will not serve by byte range. Without a
`Range` header nothing changes but an `Accept-Ranges: bytes` header.
"""

from __future__ import annotations

import pytest

from workspace_app.api import ScriptedAgentRunner, create_app
from workspace_app.api.byte_range import UNSATISFIABLE, byte_range
from workspace_app.filestore.specstar_impl import SpecstarFileStore
from workspace_app.resources import make_spec
from workspace_app.sandbox.mock import MockSandbox

from ._client import TestClient
from .conftest import register_rca_item


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 100, None),
        ("", 100, None),
        ("bytes=0-99", 100, (0, 99)),
        ("bytes=0-9", 100, (0, 9)),
        ("bytes=90-", 100, (90, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=0-500", 100, (0, 99)),  # a last byte past the end is clamped
        ("bytes=100-", 100, UNSATISFIABLE),  # a first byte past the end is not
        ("bytes=-0", 100, UNSATISFIABLE),
        ("bytes=-10", 0, UNSATISFIABLE),
        ("bytes=0-0,5-9", 100, None),  # a multi-range: the whole file, as before
        ("items=0-9", 100, None),
        ("bytes=", 100, None),
        ("bytes=9-5", 100, None),
    ],
)
def test_byte_range_reads_the_single_forms_and_nothing_else(header, size, expected):
    assert byte_range(header, size) == expected


def _client_and_item():
    spec = make_spec()
    app = create_app(
        spec=spec,
        sandbox=MockSandbox(),
        filestore=SpecstarFileStore(spec),
        runner=ScriptedAgentRunner([]),
    )
    return TestClient(app), register_rca_item(spec)


def test_a_ranged_read_is_a_206_with_the_slice_and_the_size():
    client, iid = _client_and_item()
    body = bytes(range(256)) * 4  # 1024 bytes
    assert client.put(f"/a/rca/items/{iid}/files/videos/x.mp4", content=body).status_code == 204

    r = client.get(f"/a/rca/items/{iid}/files/videos/x.mp4", headers={"Range": "bytes=10-19"})

    assert r.status_code == 206, r.text
    assert r.content == body[10:20]
    assert r.headers["content-range"] == "bytes 10-19/1024"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"


def test_the_last_bytes_and_an_open_ended_range_are_served_too():
    client, iid = _client_and_item()
    body = b"0123456789" * 10
    client.put(f"/a/rca/items/{iid}/files/a.bin", content=body)

    tail = client.get(f"/a/rca/items/{iid}/files/a.bin", headers={"Range": "bytes=-7"})
    assert (tail.status_code, tail.content) == (206, body[-7:])
    assert tail.headers["content-range"] == "bytes 93-99/100"
    rest = client.get(f"/a/rca/items/{iid}/files/a.bin", headers={"Range": "bytes=95-"})
    assert (rest.status_code, rest.content) == (206, body[95:])


def test_a_range_past_the_end_is_416_naming_the_size():
    client, iid = _client_and_item()
    client.put(f"/a/rca/items/{iid}/files/a.bin", content=b"x" * 100)

    r = client.get(f"/a/rca/items/{iid}/files/a.bin", headers={"Range": "bytes=100-"})

    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */100"


def test_without_a_range_the_whole_file_comes_back_as_before_and_ranges_are_advertised():
    client, iid = _client_and_item()
    client.put(f"/a/rca/items/{iid}/files/report.md", content=b"# hi")

    r = client.get(f"/a/rca/items/{iid}/files/report.md")

    assert r.status_code == 200 and r.content == b"# hi"
    assert r.headers["content-type"].startswith("text/markdown")
    assert r.headers["accept-ranges"] == "bytes"

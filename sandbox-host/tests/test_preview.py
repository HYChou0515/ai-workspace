"""Slide previews made and kept beside a sandbox (the app's
docs/plan-pptx-preview.md N1, N2, D1, D2): over the wire, and on the real local
backend.

The host converts a deck through the sandbox's own exec and keeps the PDF by
the deck's content hash OUTSIDE the workspace — never walked, archived or
counted, gone when the sandbox is reaped. Only the hash crosses the wire on a
conversion; the PDF is read once, by `GET /preview/{sha}`.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from httpx import ASGITransport

from sandbox_host.app import make_host_app
from sandbox_host.local_process import LocalProcessSandbox
from sandbox_host.mock import MockSandbox
from sandbox_host.protocol import PreviewFailed, SandboxSpec

DECK = b"PK\x03\x04 a deck"
SHA = hashlib.sha256(DECK).hexdigest()
FAKE_SOFFICE = ("sh", "-c", 'printf "%%PDF-fake" > "$1/$(basename "${2%.*}").pdf"', "sh")


@pytest.fixture
async def backend():
    return MockSandbox()


@pytest.fixture
async def client(backend):  # noqa: ANN001
    app = make_host_app(backend, advertise_url="http://h:8000")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://h") as c:
        yield c


async def _rid(c: httpx.AsyncClient) -> str:
    rid = (await c.post("/sandboxes", json={})).json()["remote_id"]
    await c.put(f"/sandboxes/{rid}/file", params={"path": "/q3.pptx"}, content=DECK)
    return rid


async def test_the_wire_converts_then_serves_the_preview(client) -> None:  # noqa: ANN001
    rid = await _rid(client)
    ask = {"path": "/q3.pptx"}

    looked = await client.post(f"/sandboxes/{rid}/preview", params={**ask, "convert": "false"})
    assert looked.json() == {"sha": None}
    # 204, not 404: on this wire 404 means "no such sandbox".
    assert (await client.get(f"/sandboxes/{rid}/preview/{SHA}")).status_code == 204
    made = await client.post(f"/sandboxes/{rid}/preview", params={**ask, "convert": "true"})
    got = await client.get(f"/sandboxes/{rid}/preview/{SHA}")

    assert made.json() == {"sha": SHA}
    assert got.status_code == 200 and got.content.startswith(b"%PDF")


async def test_a_failed_conversion_is_named_on_the_wire(client, backend) -> None:  # noqa: ANN001
    backend.fail_preview = "source file could not be loaded"
    rid = await _rid(client)

    r = await client.post(
        f"/sandboxes/{rid}/preview", params={"path": "/q3.pptx", "convert": "true"}
    )

    assert r.status_code == 404
    assert r.json() == {"error": "PreviewFailed", "detail": "source file could not be loaded"}


@pytest.mark.parametrize(
    ("key", "status"),
    # An encoded `/` never matches the route; any name that does is a 422.
    [("..%2F..%2Fetc%2Fpasswd", 404), ("A" * 64, 422), ("abc", 422)],
)
async def test_the_wire_refuses_a_name_that_is_not_a_content_hash(client, key, status) -> None:  # noqa: ANN001
    rid = await _rid(client)

    assert (await client.get(f"/sandboxes/{rid}/preview/{key}")).status_code == status


def _local(tmp_path, command=FAKE_SOFFICE) -> LocalProcessSandbox:  # noqa: ANN001
    return LocalProcessSandbox(root_dir=tmp_path, isolate=False, preview_command=command)


async def test_a_preview_lives_beside_the_workspace_not_in_it(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")
    before = await sandbox.disk_usage(h)

    assert await sandbox.render_preview(h, "/q3.pptx", convert=True) == SHA

    assert (await sandbox.get_preview(h, SHA) or b"").startswith(b"%PDF")
    assert [e.path for e in (await sandbox.walk(h, "/")).files] == ["/q3.pptx"]
    assert await sandbox.disk_usage(h) == before
    assert not list(tmp_path.rglob(".preview-out/*/*.pdf"))


async def test_a_failed_conversion_says_why_and_caches_nothing(tmp_path) -> None:  # noqa: ANN001
    failing = ("sh", "-c", "echo 'source file could not be loaded' >&2; exit 1", "sh")
    sandbox = _local(tmp_path, failing)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")

    with pytest.raises(PreviewFailed, match="could not be loaded"):
        await sandbox.render_preview(h, "/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_reaped_sandbox_takes_its_previews_with_it(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")
    await sandbox.render_preview(h, "/q3.pptx", convert=True)
    stored = list(tmp_path.rglob(f"{SHA}.pdf"))
    assert len(stored) == 1

    await sandbox.kill(h)

    assert not stored[0].exists()


async def test_without_convert_a_missing_preview_is_none_and_nothing_runs(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path, ("sh", "-c", "exit 1", "sh"))
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")

    assert await sandbox.render_preview(h, "/q3.pptx", convert=False) is None


async def test_a_converter_that_exits_non_zero_is_a_failure_even_with_a_pdf(tmp_path) -> None:  # noqa: ANN001
    half = (
        "sh",
        "-c",
        'printf "%%PDF-half" > "$1/$(basename "${2%.*}").pdf"; echo crashed >&2; exit 1',
        "sh",
    )
    sandbox = _local(tmp_path, half)
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/q3.pptx")

    with pytest.raises(PreviewFailed, match="crashed"):
        await sandbox.render_preview(h, "/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None
    assert not list(tmp_path.rglob(f".preview-out/{SHA}"))


async def _deck(sandbox):  # noqa: ANN001, ANN202
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/slides/q3.pptx")
    return h


# ── review round 1: what the converter leaves is untrusted ─────────────────
# Under isolation the host reads as root what a sandbox uid wrote; under the
# jail the infra area is the chroot `/`. So nothing the sandbox can plant is
# followed, and nothing kept is readable by another item.


def _root_of(tmp_path, h):  # noqa: ANN001, ANN202
    return tmp_path / h.id


async def test_a_link_left_where_the_pdf_belongs_is_not_followed(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF someone else's")
    plant = ("sh", "-c", f'ln -s {secret} "$1/$(basename "${{2%.*}}").pdf"', "sh")
    sandbox = _local(tmp_path, plant)
    h = await _deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_link_planted_in_the_cache_is_not_a_preview(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF someone else's")
    sandbox = _local(tmp_path)
    h = await _deck(sandbox)
    (_root_of(tmp_path, h) / ".preview").mkdir()
    (_root_of(tmp_path, h) / ".preview" / f"{SHA}.pdf").symlink_to(secret)

    assert await sandbox.get_preview(h, SHA) is None
    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) is None


async def test_a_hard_link_left_where_the_pdf_belongs_is_not_taken(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF someone else's")
    plant = ("sh", "-c", f'ln {secret} "$1/$(basename "${{2%.*}}").pdf"', "sh")
    sandbox = _local(tmp_path, plant)
    h = await _deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)


async def test_a_cache_dir_that_is_a_link_is_not_read_through(tmp_path) -> None:  # noqa: ANN001
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / f"{SHA}.pdf").write_bytes(b"%PDF someone else's")
    sandbox = _local(tmp_path)
    h = await _deck(sandbox)
    (_root_of(tmp_path, h) / ".preview").symlink_to(elsewhere)

    assert await sandbox.get_preview(h, SHA) is None
    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) is None


async def test_a_kept_preview_is_readable_by_its_owner_only(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    store = _root_of(tmp_path, h) / ".preview"
    assert store.stat().st_mode & 0o777 == 0o700
    assert (store / f"{SHA}.pdf").stat().st_mode & 0o777 == 0o600


async def test_something_that_is_not_a_pdf_is_not_kept(tmp_path) -> None:  # noqa: ANN001
    junk = ("sh", "-c", 'printf "not a pdf" > "$1/$(basename "${2%.*}").pdf"', "sh")
    sandbox = _local(tmp_path, junk)
    h = await _deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


# LibreOffice refuses a profile another instance holds: this stand-in takes its
# profile dir the way soffice locks it, so two conversions sharing one fail.
LOCKING_SOFFICE = (
    "sh",
    "-c",
    'p="${3#-env:UserInstallation=file://}"; mkdir "$p" || { echo "profile in use" >&2; exit 1; };'
    ' sleep 0.3; printf "%%PDF-fake" > "$1/$(basename "${2%.*}").pdf"',
    "sh",
)


async def test_two_conversions_at_once_each_get_their_own_profile(tmp_path) -> None:  # noqa: ANN001
    import asyncio

    sandbox = _local(tmp_path, LOCKING_SOFFICE)
    h = await _deck(sandbox)
    await sandbox.upload(h, DECK + b" two", "/slides/q4.pptx")

    made = await asyncio.gather(
        sandbox.render_preview(h, "/slides/q3.pptx", convert=True),
        sandbox.render_preview(h, "/slides/q4.pptx", convert=True),
    )

    assert all(made)


async def test_the_converter_s_temp_files_stay_out_of_the_workspace(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Isolation points TMPDIR at the workspace; the converter must not use it."""
    litter = (
        "sh",
        "-c",
        'touch "$TMPDIR/lu-tmp"; printf "%%PDF-fake" > "$1/$(basename "${2%.*}").pdf"',
        "sh",
    )
    sandbox = _local(tmp_path, litter)
    h = await _deck(sandbox)
    monkeypatch.setenv("TMPDIR", str(_root_of(tmp_path, h) / "root"))

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert [f.path for f in (await sandbox.walk(h, "/")).files] == ["/slides/q3.pptx"]


async def test_a_quiet_converter_is_not_taken_for_a_hung_one(tmp_path) -> None:  # noqa: ANN001
    """LibreOffice prints nothing while it works; the idle cap must not kill it
    before the conversion's own time limit."""
    quiet = ("sh", "-c", 'sleep 1.5; printf "%%PDF-fake" > "$1/$(basename "${2%.*}").pdf"', "sh")
    sandbox = LocalProcessSandbox(
        root_dir=tmp_path, isolate=False, preview_command=quiet, log_timeout=0.5
    )
    h = await _deck(sandbox)

    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=True) == SHA


async def test_nothing_of_a_conversion_is_left_behind(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert list((_root_of(tmp_path, h) / ".home" / ".preview-out").iterdir()) == []


async def test_a_pdf_bigger_than_the_preview_limit_is_not_read_or_kept(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """What the sandbox leaves can be any size — a sparse file costs it nothing
    and the host would read every byte of it."""
    import sandbox_host.local_process as lp

    monkeypatch.setattr(lp, "PREVIEW_MAX_BYTES", 1024)
    huge = (
        "sh",
        "-c",
        'f="$1/$(basename "${2%.*}").pdf"; printf "%%PDF-" > "$f"; truncate -s 1G "$f"',
        "sh",
    )
    sandbox = _local(tmp_path, huge)
    h = await _deck(sandbox)

    with pytest.raises(PreviewFailed, match="too large"):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_preview_that_cannot_be_kept_leaves_nothing_half_written(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _deck(sandbox)
    (_root_of(tmp_path, h) / ".preview" / f"{SHA}.pdf" / "x").mkdir(parents=True)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert [p.name for p in (_root_of(tmp_path, h) / ".preview").iterdir()] == [f"{SHA}.pdf"]


@pytest.mark.parametrize("target", ["outside", "/dev/zero"])
async def test_a_deck_that_is_a_link_is_not_followed(tmp_path, target) -> None:  # noqa: ANN001
    """The deck is the sandbox's file too: hashing through a link would read
    whatever it names, as the host — another item's file, or /dev/zero forever."""
    outside = tmp_path / "outside.pptx"
    outside.write_bytes(DECK)
    sandbox = _local(tmp_path)
    h = await sandbox.create(SandboxSpec())
    link = _root_of(tmp_path, h) / "root" / "q3.pptx"
    link.symlink_to(outside if target == "outside" else target)

    with pytest.raises(PreviewFailed, match="not a regular file"):
        await sandbox.render_preview(h, "/q3.pptx", convert=False)


async def test_a_deck_that_is_a_folder_is_not_a_deck(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await sandbox.create(SandboxSpec())
    (_root_of(tmp_path, h) / "root" / "q3.pptx").mkdir()

    with pytest.raises(PreviewFailed, match="not a regular file"):
        await sandbox.render_preview(h, "/q3.pptx", convert=False)


async def test_an_output_dir_swapped_for_a_link_is_not_read_through(tmp_path) -> None:  # noqa: ANN001
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "q3.pdf").write_bytes(b"%PDF someone else's")
    swap = ("sh", "-c", f'rmdir "$1" && ln -s {elsewhere} "$1"', "sh")
    sandbox = _local(tmp_path, swap)
    h = await _deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None
    assert (elsewhere / "q3.pdf").exists()

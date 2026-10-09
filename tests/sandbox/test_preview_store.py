"""Slide previews, made and kept beside the sandbox (docs/plan-pptx-preview.md
N1, N2, D1, D2).

The sandbox converts a deck itself — through its own exec, under its own uid
and limits — and keeps the PDF OUTSIDE the workspace, named by the deck's
content hash: never in the file tree, never synced, never counted, gone when
the sandbox is reaped. The PDF never travels through a command's stdout. Every
backend keeps the same promise, so one set of tests runs against each.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from httpx import ASGITransport

from workspace_app.sandbox.http_client import HttpSandbox
from workspace_app.sandbox.local_process import LocalProcessSandbox
from workspace_app.sandbox.mock import MockSandbox
from workspace_app.sandbox.protocol import PreviewFailed, SandboxSpec

DECK = b"PK\x03\x04 a deck"
SHA = hashlib.sha256(DECK).hexdigest()

# What the local backend runs instead of `soffice` in a unit test: it is handed
# the output dir and the deck, and writes `<stem>.pdf` there — soffice's shape.
FAKE_SOFFICE = (
    "sh",
    "-c",
    'printf "%%PDF-fake" > "$1/$(basename "${2%.*}").pdf"; echo ran >> "$1/../../../count"',
    "sh",
)
FAILING_SOFFICE = ("sh", "-c", "echo 'source file could not be loaded' >&2; exit 1", "sh")


@pytest.fixture(params=["mock", "local", "http"])
async def sandbox(request, tmp_path):  # noqa: ANN001, ANN201
    if request.param == "mock":
        yield MockSandbox()
    elif request.param == "local":
        yield LocalProcessSandbox(root_dir=tmp_path, isolate=False, preview_command=FAKE_SOFFICE)
    else:
        from tests.sandbox.test_http import _ADVERTISE, _fake_host

        app = _fake_host(MockSandbox(), _ADVERTISE)
        async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
            yield HttpSandbox(base_url=_ADVERTISE, client=client)


async def _with_deck(sandbox):  # noqa: ANN001, ANN202
    h = await sandbox.create(SandboxSpec())
    await sandbox.upload(h, DECK, "/slides/q3.pptx")
    return h


async def test_a_deck_not_yet_converted_has_no_preview(sandbox) -> None:  # noqa: ANN001
    h = await _with_deck(sandbox)

    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) is None


async def test_converting_names_the_preview_by_the_deck_s_content(sandbox) -> None:  # noqa: ANN001
    h = await _with_deck(sandbox)

    sha = await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert sha == SHA
    pdf = await sandbox.get_preview(h, SHA)
    assert pdf is not None and pdf.startswith(b"%PDF")


async def test_a_converted_deck_is_found_without_converting_again(sandbox) -> None:  # noqa: ANN001
    h = await _with_deck(sandbox)
    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) == SHA


async def test_an_edited_deck_is_a_new_preview(sandbox) -> None:  # noqa: ANN001
    h = await _with_deck(sandbox)
    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    await sandbox.upload(h, DECK + b" edited", "/slides/q3.pptx")

    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) is None


async def test_a_preview_is_not_a_workspace_file(sandbox) -> None:  # noqa: ANN001
    h = await _with_deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    walked = await sandbox.walk(h, "/")
    assert [f.path for f in walked.files] == ["/slides/q3.pptx"]


async def test_a_missing_deck_is_file_not_found(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec())

    with pytest.raises(FileNotFoundError):
        await sandbox.render_preview(h, "/nope.pptx", convert=True)


@pytest.mark.parametrize("key", ["../../etc/passwd", "A" * 64, "abc", SHA + "/x", ""])
async def test_only_a_content_hash_names_a_preview(sandbox, key: str) -> None:  # noqa: ANN001
    """The name reaches a path on disk; nothing but 64 lowercase hex may."""
    h = await sandbox.create(SandboxSpec())

    with pytest.raises(ValueError):
        await sandbox.get_preview(h, key)


async def test_a_reaped_sandbox_takes_its_previews_with_it(sandbox) -> None:  # noqa: ANN001
    h = await sandbox.create(SandboxSpec(), sandbox_id="item-1")
    await sandbox.upload(h, DECK, "/q3.pptx")
    await sandbox.render_preview(h, "/q3.pptx", convert=True)

    await sandbox.kill(h)
    again = await sandbox.create(SandboxSpec(), sandbox_id="item-1")

    assert await sandbox.get_preview(again, SHA) is None


# ── the local backend: the real exec path ──────────────────────────────────


def _local(tmp_path, command=FAKE_SOFFICE) -> LocalProcessSandbox:  # noqa: ANN001
    return LocalProcessSandbox(root_dir=tmp_path, isolate=False, preview_command=command)


async def test_the_deck_is_converted_once_however_often_it_is_asked_for(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)

    for _ in range(3):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    runs = list(tmp_path.rglob("count"))
    assert len(runs) == 1 and runs[0].read_text().count("ran") == 1


async def test_a_failed_conversion_says_why_and_caches_nothing(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path, FAILING_SOFFICE)
    h = await _with_deck(sandbox)

    with pytest.raises(PreviewFailed, match="could not be loaded"):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_preview_is_not_charged_to_the_workspace(tmp_path) -> None:  # noqa: ANN001
    """Measured where the quota measures (`disk_usage`), on a real disk; the
    converter's scratch output is gone too."""
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)
    before = await sandbox.disk_usage(h)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.disk_usage(h) == before
    assert not list(tmp_path.rglob(".preview-out/*/*.pdf"))


async def test_the_failure_is_named_over_the_wire_too() -> None:
    from tests.sandbox.test_http import _ADVERTISE, _fake_host

    backend = MockSandbox()
    backend.fail_preview = "source file could not be loaded"
    app = _fake_host(backend, _ADVERTISE)
    async with httpx.AsyncClient(transport=ASGITransport(app=app)) as client:
        sandbox = HttpSandbox(base_url=_ADVERTISE, client=client)
        h = await _with_deck(sandbox)

        with pytest.raises(PreviewFailed, match="could not be loaded"):
            await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)


async def test_a_converter_that_exits_non_zero_is_a_failure_even_with_a_pdf(tmp_path) -> None:  # noqa: ANN001
    """A crash part-way can leave a PDF behind; it is not the deck, so it is not kept."""
    half = (
        "sh",
        "-c",
        'printf "%%PDF-half" > "$1/$(basename "${2%.*}").pdf"; echo crashed >&2; exit 1',
        "sh",
    )
    sandbox = _local(tmp_path, half)
    h = await _with_deck(sandbox)

    with pytest.raises(PreviewFailed, match="crashed"):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_the_converter_s_scratch_output_is_cleared(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert not list(tmp_path.rglob(f".preview-out/{SHA}"))


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
    h = await _with_deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_link_planted_in_the_cache_is_not_a_preview(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF someone else's")
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)
    (_root_of(tmp_path, h) / ".preview").mkdir()
    (_root_of(tmp_path, h) / ".preview" / f"{SHA}.pdf").symlink_to(secret)

    assert await sandbox.get_preview(h, SHA) is None
    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) is None


async def test_a_hard_link_left_where_the_pdf_belongs_is_not_taken(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF someone else's")
    plant = ("sh", "-c", f'ln {secret} "$1/$(basename "${{2%.*}}").pdf"', "sh")
    sandbox = _local(tmp_path, plant)
    h = await _with_deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)


async def test_a_cache_dir_that_is_a_link_is_not_read_through(tmp_path) -> None:  # noqa: ANN001
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / f"{SHA}.pdf").write_bytes(b"%PDF someone else's")
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)
    (_root_of(tmp_path, h) / ".preview").symlink_to(elsewhere)

    assert await sandbox.get_preview(h, SHA) is None
    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=False) is None


async def test_a_kept_preview_is_readable_by_its_owner_only(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    store = _root_of(tmp_path, h) / ".preview"
    assert store.stat().st_mode & 0o777 == 0o700
    assert (store / f"{SHA}.pdf").stat().st_mode & 0o777 == 0o600


async def test_something_that_is_not_a_pdf_is_not_kept(tmp_path) -> None:  # noqa: ANN001
    junk = ("sh", "-c", 'printf "not a pdf" > "$1/$(basename "${2%.*}").pdf"', "sh")
    sandbox = _local(tmp_path, junk)
    h = await _with_deck(sandbox)

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
    h = await _with_deck(sandbox)
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
    h = await _with_deck(sandbox)
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
    h = await _with_deck(sandbox)

    assert await sandbox.render_preview(h, "/slides/q3.pptx", convert=True) == SHA


async def test_nothing_of_a_conversion_is_left_behind(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert list((_root_of(tmp_path, h) / ".home" / ".preview-out").iterdir()) == []


async def test_a_pdf_bigger_than_the_preview_limit_is_not_read_or_kept(
    tmp_path, monkeypatch
) -> None:  # noqa: ANN001
    """What the sandbox leaves can be any size — a sparse file costs it nothing
    and the host would read every byte of it."""
    import workspace_app.sandbox.local_process as lp

    monkeypatch.setattr(lp, "PREVIEW_MAX_BYTES", 1024)
    huge = (
        "sh",
        "-c",
        'f="$1/$(basename "${2%.*}").pdf"; printf "%%PDF-" > "$f"; truncate -s 1G "$f"',
        "sh",
    )
    sandbox = _local(tmp_path, huge)
    h = await _with_deck(sandbox)

    with pytest.raises(PreviewFailed, match="too large"):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None


async def test_a_preview_that_cannot_be_kept_leaves_nothing_half_written(tmp_path) -> None:  # noqa: ANN001
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)
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
    h = await _with_deck(sandbox)

    with pytest.raises(PreviewFailed):
        await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert await sandbox.get_preview(h, SHA) is None
    assert (elsewhere / "q3.pdf").exists()


async def test_the_converter_s_pdf_is_read_no_further_than_the_limit(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Refusing after reading the whole file would already have cost the memory:
    what is pinned is how much is asked for."""
    import workspace_app.sandbox.local_process as lp

    asked: list[int] = []
    real_fdopen = lp.os.fdopen

    class _Counting:
        def __init__(self, f) -> None:  # noqa: ANN001
            self._f = f

        def __enter__(self):  # noqa: ANN204
            self._f.__enter__()
            return self

        def __exit__(self, *exc) -> None:  # noqa: ANN002
            self._f.__exit__(*exc)

        def fileno(self) -> int:
            return self._f.fileno()

        def read(self, n: int = -1) -> bytes:
            asked.append(n)
            return self._f.read(n)

        def write(self, data: bytes) -> int:
            return self._f.write(data)

    monkeypatch.setattr(
        lp.os, "fdopen", lambda fd, mode="r", *a, **k: _Counting(real_fdopen(fd, mode, *a, **k))
    )
    sandbox = _local(tmp_path)
    h = await _with_deck(sandbox)

    await sandbox.render_preview(h, "/slides/q3.pptx", convert=True)

    assert lp.PREVIEW_MAX_BYTES + 1 in asked and -1 not in asked

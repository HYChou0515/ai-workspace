"""Inline attached images for a vision main model (source A): the send path reads
each attached workspace image and turns it into a `data:` URL so the runner can
inline it into the turn's user message — the model sees the pixels directly, no
`read_image` round-trip through the separate VLM."""

from __future__ import annotations

import base64

from workspace_app.api.chat_send import _load_inline_image_urls
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore

# A real 1×1 PNG — libmagic sniffs it as image/png.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def test_load_inline_image_urls_builds_a_data_url_per_image():
    files = WorkspaceFiles(MemoryFileStore())
    await files.write("inv", "/shot.png", _PNG)

    urls = await _load_inline_image_urls(files, "inv", ["/shot.png"])

    assert urls == [f"data:image/png;base64,{base64.b64encode(_PNG).decode('ascii')}"]


async def test_load_inline_image_urls_skips_non_images():
    """A non-image path (the model would call read_file/list_files on it) is
    dropped rather than inlined — only real images become image parts."""
    files = WorkspaceFiles(MemoryFileStore())
    await files.write("inv", "/shot.png", _PNG)
    await files.write("inv", "/notes.txt", b"just some plain text, not an image")

    urls = await _load_inline_image_urls(files, "inv", ["/notes.txt", "/shot.png"])

    assert urls == [f"data:image/png;base64,{base64.b64encode(_PNG).decode('ascii')}"]


async def test_load_inline_image_urls_skips_a_missing_attachment():
    """An attachment that vanished (deleted between upload and send) is skipped,
    not fatal — the turn still runs with whatever images survive."""
    files = WorkspaceFiles(MemoryFileStore())
    await files.write("inv", "/shot.png", _PNG)

    urls = await _load_inline_image_urls(files, "inv", ["/gone.png", "/shot.png"])

    assert urls == [f"data:image/png;base64,{base64.b64encode(_PNG).decode('ascii')}"]

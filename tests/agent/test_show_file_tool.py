"""`show_file` — the agent puts a workspace file in front of the user.

The result is a `shown_files` declaration the FE renders. Declaring is separate
from succeeding: an unresolvable path is an error that declares nothing, so the
FE is never handed a card it would draw as a broken image.
"""

from __future__ import annotations

import base64
import json

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, show_file_impl
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore

# A real 1×1 PNG — libmagic sniffs it as image/png.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
# Enough of a PDF header for libmagic to call it application/pdf.
_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


async def _ctx() -> tuple[RunContextWrapper, WorkspaceFiles]:
    files = WorkspaceFiles(MemoryFileStore())
    return RunContextWrapper(AgentToolContext(investigation_id="inv-1", files=files)), files


def _declared(out: str) -> list[dict]:
    """The `shown_files` the tool declared, or [] when it declared nothing."""
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    shown = parsed.get("shown_files", [])
    assert isinstance(shown, list)
    return shown


async def test_show_file_declares_an_image_for_the_chat_to_render():
    """The declaration carries everything the FE needs to render without a second
    round-trip: path, mime, size, caption."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/out/revenue.png", _PNG)

    out = await show_file_impl(ctx, "out/revenue.png", caption="月營收趨勢")

    assert _declared(out) == [
        {
            "path": "/out/revenue.png",
            "mime": "image/png",
            "size": len(_PNG),
            "caption": "月營收趨勢",
        }
    ]


async def test_show_file_takes_any_file_not_only_images():
    """The capability is files, not images. The mime rides along so a pdf renders
    as a card-with-opener rather than an `<img>` that can't load."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/out/Q3-report.pdf", _PDF)

    out = await show_file_impl(ctx, "/out/Q3-report.pdf")

    [shown] = _declared(out)
    assert shown["path"] == "/out/Q3-report.pdf"
    assert shown["mime"] == "application/pdf"
    assert shown["size"] == len(_PDF)


async def test_show_file_normalises_the_path_the_frontend_will_fetch():
    """The agent writes relative paths (#549); the FE's openFile/fileUrl seams take
    absolute ones. Whichever dialect arrives, the declared path is the FE's."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/notes/diagram.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")

    for dialect in ("notes/diagram.svg", "./notes/diagram.svg", "/notes/diagram.svg"):
        [shown] = _declared(await show_file_impl(ctx, dialect))
        assert shown["path"] == "/notes/diagram.svg", dialect


async def test_show_file_declares_nothing_when_the_file_is_missing():
    """An unresolvable path must not reach the FE as a card. The agent gets a
    plain error it can act on instead."""
    ctx, _ = await _ctx()

    out = await show_file_impl(ctx, "/out/never-written.png")

    assert out.startswith("error:")
    assert "never-written.png" in out
    assert "shown_files" not in out


async def test_show_file_omits_an_absent_caption():
    """Absent means absent — the FE shows the filename alone, not an empty line."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/a.png", _PNG)

    [shown] = _declared(await show_file_impl(ctx, "/a.png"))

    assert "caption" not in shown


async def test_show_file_tells_the_agent_the_user_can_now_see_it():
    """The result doubles as the model's feedback: told the file is visible, a
    model stops following up by narrating its contents."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/a.png", _PNG)

    note = json.loads(await show_file_impl(ctx, "/a.png"))["note"]

    assert "a.png" in note
    # Stated in the agent's own relative dialect (#549), not the internal form.
    assert "/a.png" not in note


async def test_show_file_exercises_the_read_content_verb():
    """Showing a file is a read of it, so it rides the same funnel as read_file —
    the agent is not a way around the speaker's own grants (#309)."""
    from workspace_app.agent.tool_authz import TOOL_VERBS

    assert TOOL_VERBS["show_file"] == "read_content"

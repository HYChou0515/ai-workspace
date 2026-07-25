"""`show_file` — the agent's "put this workspace file in front of the user"
capability.

Before this tool the only way a produced file reached the chat was the FE
regex-sniffing every tool's output text for a `"images": [...]`-shaped JSON
array (`web/src/renderers/toolImages.ts`). That never fired for the common
case — the agent writes a python script, `exec`s it, and the stdout just says
"saved to /out/chart.png" — so the user was told a path and had to go dig the
file out of the file tree themselves.

The tool DECLARES what to render, in one structured place the FE can trust:
its result is a `shown_files` list. Declaring is deliberately separate from
succeeding — a path that doesn't resolve is reported as an error and declares
NOTHING, so the FE can never be handed a card it will render as a broken image.
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
    """The happy path: the file resolves, so the tool declares it with everything
    the FE needs to render without a second round-trip — the path to fetch it by,
    the mime that decides inline-image vs card, its size, and the agent's caption."""
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
    """The capability is "show a workspace FILE", not "show an image" — a report
    the agent produced is exactly as showable as a chart. The mime rides along so
    the FE renders a pdf as a card-with-opener instead of an <img> that can't load."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/out/Q3-report.pdf", _PDF)

    out = await show_file_impl(ctx, "/out/Q3-report.pdf")

    [shown] = _declared(out)
    assert shown["path"] == "/out/Q3-report.pdf"
    assert shown["mime"] == "application/pdf"
    assert shown["size"] == len(_PDF)


async def test_show_file_normalises_the_path_the_frontend_will_fetch():
    """The agent's path dialect is RELATIVE (#549) but the FE's openFile/fileUrl
    seams take workspace-absolute paths. Normalising here means neither side has
    to guess: whatever dialect the agent used, the declared path is the one the
    FE can hand straight to those seams."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/notes/diagram.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")

    for dialect in ("notes/diagram.svg", "./notes/diagram.svg", "/notes/diagram.svg"):
        [shown] = _declared(await show_file_impl(ctx, dialect))
        assert shown["path"] == "/notes/diagram.svg", dialect


async def test_show_file_declares_nothing_when_the_file_is_missing():
    """A path that doesn't resolve must not reach the FE as a card — that is
    precisely the broken-image failure the old regex sniffer had, since it
    matched path-shaped TEXT and never checked whether the file was there.
    The agent gets a plain error it can act on instead."""
    ctx, _ = await _ctx()

    out = await show_file_impl(ctx, "/out/never-written.png")

    assert out.startswith("error:")
    assert "never-written.png" in out
    assert "shown_files" not in out


async def test_show_file_omits_an_absent_caption():
    """`caption` is the agent's one-line "what am I looking at". Absent means
    absent — the FE shows the filename alone rather than an empty caption line."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/a.png", _PNG)

    [shown] = _declared(await show_file_impl(ctx, "/a.png"))

    assert "caption" not in shown


async def test_show_file_tells_the_agent_the_user_can_now_see_it():
    """The result doubles as the model's feedback. Without a plain statement that
    the file is now VISIBLE, a model that just called the tool goes on to describe
    the file in prose or re-`read_file`s it to paraphrase — the exact busywork the
    tool exists to remove."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/a.png", _PNG)

    note = json.loads(await show_file_impl(ctx, "/a.png"))["note"]

    assert "a.png" in note
    # Stated in the agent's own relative dialect (#549), not the internal form.
    assert "/a.png" not in note


async def test_show_file_exercises_the_read_content_verb():
    """Showing a file to the user is a READ of that file, so it goes through the
    same permission funnel as read_file — a speaker who may not read the item's
    content cannot use the agent as a way to look at it anyway (#309)."""
    from workspace_app.agent.tool_authz import TOOL_VERBS

    assert TOOL_VERBS["show_file"] == "read_content"

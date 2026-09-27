"""`show_file` — the agent puts a workspace file in front of the user.

The result is a sentence for the model plus a `[shown-files]` declaration the FE
renders. Declaring is separate from succeeding: an unresolvable path is an error
that declares nothing, so the FE is never handed a card it would draw as broken.
"""

from __future__ import annotations

import base64
import json

from agents import RunContextWrapper

from workspace_app.agent import AgentToolContext, show_file_impl
from workspace_app.agent.shown_files import SHOWN_FILES_MARKER
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
    _head, sep, payload = out.partition(SHOWN_FILES_MARKER)
    if not sep:
        return []
    shown = json.loads(payload)["shown_files"]
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
    assert SHOWN_FILES_MARKER not in out


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

    note = (await show_file_impl(ctx, "/a.png")).partition(SHOWN_FILES_MARKER)[0]

    assert "a.png" in note
    # Stated in the agent's own relative dialect (#549), not the internal form.
    assert "/a.png" not in note


async def test_show_file_exercises_the_read_content_verb():
    """Showing a file is a read of it, so it rides the same funnel as read_file —
    the agent is not a way around the speaker's own grants (#309)."""
    from workspace_app.agent.tool_authz import TOOL_VERBS

    # A tuple: the table carries every verb a tool exercises, because a tool that
    # reads AND writes has to name both or the ceiling under-describes it.
    assert TOOL_VERBS["show_file"] == ("read_content",)


async def test_show_file_keeps_the_declaration_out_of_what_the_model_reads():
    """The declaration is a trailing marker line, so the sentence the model reads
    is not JSON — a tool result the model has to parse is one it can misread."""
    ctx, files = await _ctx()
    await files.write("inv-1", "/a.png", _PNG)

    out = await show_file_impl(ctx, "/a.png")

    assert out.splitlines()[0] == "a.png is now displayed in the chat — the user can see it."
    assert out.count(SHOWN_FILES_MARKER) == 1


# --- #847 PR 3 P4: `show_file(layout=…)` — one card that opens an arrangement ---


def _layout(tree: dict):
    """What the SDK hands the impl: the model's JSON validated into the model."""
    from workspace_app.agent.shown_files import PaneLayout

    return PaneLayout.model_validate(tree)


def _leaf(path: str) -> dict:
    return {"type": "leaf", "path": path, "dir": None, "ratio": None, "a": None, "b": None}


def _split(dir: str, a: dict, b: dict, ratio: float | None = None) -> dict:
    return {"type": "split", "path": None, "dir": dir, "ratio": ratio, "a": a, "b": b}


def _declaration(out: str) -> dict:
    _head, sep, payload = out.partition(SHOWN_FILES_MARKER)
    assert sep, out
    return json.loads(payload)


async def _three_files():
    ctx, files = await _ctx()
    for p in ("/v/grid.png", "/v/scatter.png", "/v/table.pdf"):
        await files.write("inv-1", p, _PDF if p.endswith(".pdf") else _PNG)
    return ctx


async def test_show_file_layout_declares_the_tree_and_every_file():
    """The marker carries the tree (paths as leaves, absolute like `path`) AND
    every leaf in `shown_files`, so a consumer that only knows the flat list
    still sees each file — the chat video and chat export read that list."""
    ctx = await _three_files()
    tree = _split(
        "row",
        _split("col", _leaf("v/grid.png"), _leaf("./v/scatter.png"), 0.4),
        _leaf("/v/table.pdf"),
    )

    out = await show_file_impl(ctx, layout=_layout(tree), caption="fail rate, linked")

    decl = _declaration(out)
    assert decl["layout"] == {
        "type": "split",
        "dir": "row",
        "ratio": 0.5,
        "a": {
            "type": "split",
            "dir": "col",
            "ratio": 0.4,
            "a": {"type": "leaf", "path": "/v/grid.png"},
            "b": {"type": "leaf", "path": "/v/scatter.png"},
        },
        "b": {"type": "leaf", "path": "/v/table.pdf"},
    }
    assert [f["path"] for f in decl["shown_files"]] == [
        "/v/grid.png",
        "/v/scatter.png",
        "/v/table.pdf",
    ]
    assert decl["shown_files"][2]["mime"] == "application/pdf"
    # The caption names the arrangement, so it rides on the layout, once.
    assert decl["caption"] == "fail rate, linked"
    assert all("caption" not in f for f in decl["shown_files"])


async def test_show_file_layout_tells_the_agent_what_is_on_screen():
    ctx = await _three_files()
    tree = _split("row", _leaf("v/grid.png"), _leaf("v/table.pdf"))

    note = (await show_file_impl(ctx, layout=_layout(tree))).partition(SHOWN_FILES_MARKER)[0]

    assert note.startswith("A layout of 2 files")
    assert "v/grid.png" in note and "v/table.pdf" in note
    assert "/v/grid.png" not in note  # the agent's relative dialect (#549)


async def test_show_file_layout_one_missing_leaf_declares_nothing():
    """One failure declares nothing: a card whose third pane is empty is the
    broken card the single-path rule already refuses to draw."""
    ctx = await _three_files()
    tree = _split("row", _leaf("v/grid.png"), _leaf("v/gone.png"))

    out = await show_file_impl(ctx, layout=_layout(tree))

    assert out.startswith("error:")
    assert "v/gone.png" in out
    assert SHOWN_FILES_MARKER not in out


async def test_show_file_layout_rejects_malformed_nodes():
    ctx = await _three_files()
    ok = _split("row", _leaf("v/grid.png"), _leaf("v/table.pdf"))
    cases = {
        "a leaf with no path": _split("row", _leaf(""), _leaf("v/table.pdf")),
        "a leaf that also splits": _split(
            "row", _leaf("v/grid.png") | {"a": _leaf("x")}, _leaf("v/table.pdf")
        ),
        "a split missing a side": ok | {"b": None},
        "a split with no dir": ok | {"dir": None},
        "a split that also names a path": ok | {"path": "v/grid.png"},
        "a ratio outside (0, 1)": ok | {"ratio": 1.0},
        "the same file twice": _split("row", _leaf("v/grid.png"), _leaf("./v/grid.png")),
        "a single leaf": _leaf("v/grid.png"),
    }
    for what, tree in cases.items():
        out = await show_file_impl(ctx, layout=_layout(tree))
        assert out.startswith("error: layout"), (what, out)
        assert SHOWN_FILES_MARKER not in out, what


async def test_show_file_needs_exactly_one_of_path_and_layout():
    ctx = await _three_files()
    tree = _split("row", _leaf("v/grid.png"), _leaf("v/table.pdf"))
    both = await show_file_impl(ctx, "v/grid.png", layout=_layout(tree))
    neither = await show_file_impl(ctx)
    for out in (both, neither):
        assert out.startswith("error:")
        assert SHOWN_FILES_MARKER not in out


def test_show_file_schema_offers_layout_without_refs_and_depth_bounded():
    """The emitted schema is what the model sees. #613 inlines `$defs` for local
    chat templates, so a layout must reach the model as plain nested objects —
    a recursive type would leave a dangling `$ref` once the defs are dropped.
    Bounded at three levels of split (eight panes)."""
    from workspace_app.agent.tools import build_tools

    [tool] = build_tools(["show_file"])
    schema = tool.params_json_schema
    assert "$ref" not in json.dumps(schema)
    layout = next(v for v in schema["properties"]["layout"]["anyOf"] if v.get("type") == "object")
    depth, node = 0, layout
    while "a" in node["properties"]:
        node = next(v for v in node["properties"]["a"]["anyOf"] if v.get("type") == "object")
        depth += 1
    assert depth == 3
    assert set(node["properties"]) == {"type", "path"}


def test_inlining_refuses_a_recursive_schema_instead_of_leaving_a_dangling_ref():
    """`$defs` is dropped after inlining, so a self-reference cut off at some
    depth points at nothing. The only honest outcome is to refuse it."""
    import pytest

    from workspace_app.agent.tools import _inline_schema_refs

    schema = {
        "$defs": {"N": {"type": "object", "properties": {"n": {"$ref": "#/$defs/N"}}}},
        "type": "object",
        "properties": {"root": {"$ref": "#/$defs/N"}},
    }
    with pytest.raises(ValueError, match="recursive tool schema"):
        _inline_schema_refs(schema)
    # The same def used twice side by side is not a cycle.
    flat = {
        "$defs": {"L": {"type": "string"}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/L"}, "b": {"$ref": "#/$defs/L"}},
    }
    assert _inline_schema_refs(flat)["properties"] == {
        "a": {"type": "string"},
        "b": {"type": "string"},
    }

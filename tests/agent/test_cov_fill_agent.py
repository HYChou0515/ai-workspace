"""Characterization tests filling coverage gaps in the agent layer.

Targets (uncovered before this file):
  - tools.py: search_wiki error/skip branches (268-269, 274-275, 278-279),
    read_source missing-sources / missing-ref (322, 337), _read_step_names
    short-row + blank-line loop branches (483->482, 487->486),
    _parse_module_json garbage/non-dict (504-505, 507), infer_modules
    empty-steps error (606).

litellm is mocked / the sub-agent run is faked — no real LLM, per the repo
conventions (ScriptedAgentRunner / HashEmbedder style).
"""

from __future__ import annotations

from types import SimpleNamespace

from agents import RunContextWrapper, function_tool

from workspace_app.agent import AgentToolContext
from workspace_app.agent.tools import (
    _parse_module_json,
    _read_step_names,
    infer_modules_impl,
    read_source_impl,
    search_wiki_impl,
)
from workspace_app.files import WorkspaceFiles
from workspace_app.filestore.memory import MemoryFileStore


@function_tool
def write_file(path: str, content: str) -> str:
    """Create a file."""
    return "ok"


def _resp(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


# ── tools.py: search_wiki branches ───────────────────────────────────


class _FlakyFiles:
    """A minimal files facade for the search_wiki skip branches: `ls` lists a
    path that `read` then raises FileNotFound for (a delete race), plus a path
    holding invalid UTF-8 bytes."""

    def __init__(self, paths: list[str], reads: dict[str, bytes]) -> None:
        self._paths = paths
        self._reads = reads

    async def ls(self, inv: str, prefix: str = "") -> list[str]:
        return list(self._paths)

    async def read(self, inv: str, path: str) -> bytes:
        from workspace_app.filestore.protocol import FileNotFound

        if path not in self._reads:
            raise FileNotFound(path)
        return self._reads[path]


def _ctx(**kw) -> RunContextWrapper[AgentToolContext]:
    return RunContextWrapper(AgentToolContext(investigation_id="wiki:c1", **kw))


async def test_search_wiki_reports_invalid_query(monkeypatch):
    """lines 268-269: an InvalidQuery from compile_query is reported, not raised."""
    import workspace_app.api.search as search_mod

    def boom(*_a, **_k):
        raise search_mod.InvalidQuery("bad pattern")

    monkeypatch.setattr(search_mod, "compile_query", boom)
    ctx = _ctx(files=WorkspaceFiles(MemoryFileStore()))
    out = await search_wiki_impl(ctx, "anything")
    assert out.startswith("error: invalid search")
    assert "bad pattern" in out


async def test_search_wiki_skips_unreadable_and_undecodable_pages():
    """lines 274-275 (FileNotFound → continue) and 278-279 (UnicodeDecodeError
    → continue): a listed page that vanished and one with non-UTF-8 bytes are
    both skipped; the matching readable page still surfaces."""
    files = _FlakyFiles(
        paths=["/gone.md", "/binary.md", "/good.md"],
        reads={
            "/binary.md": b"\xff\xfe not utf8",  # UnicodeDecodeError on decode
            "/good.md": b"reflow zone 3\n",
            # "/gone.md" intentionally absent → FileNotFound on read
        },
    )
    ctx = _ctx(files=files)
    out = await search_wiki_impl(ctx, "reflow")
    assert "good.md:1:" in out
    assert "/gone.md" not in out
    assert "/binary.md" not in out


# ── tools.py: read_source error branches ─────────────────────────────


async def test_read_source_errors_when_no_sources_wired():
    """line 322: with no wiki_sources on the context, read_source returns a
    not-found error instead of crashing."""
    ctx = _ctx()
    out = await read_source_impl(ctx, "anything.md")
    assert out == "error: source not found: anything.md"


async def test_read_source_reader_mode_errors_on_missing_ref():
    """line 337: reader mode (wiki_cite_sources) where the source has no ref
    (unknown path) → not-found error."""
    from workspace_app.kb.wiki.sources import IWikiSources, WikiSourceRef

    class _EmptySources(IWikiSources):
        def list(self) -> list[str]:  # ty: ignore[invalid-type-form]
            return []

        def read(self, path: str) -> str | None:
            return None

        def ref(self, path: str) -> WikiSourceRef | None:
            return None  # never resolves → reader path hits line 337

        def ref_by_id(self, doc_id: str) -> WikiSourceRef | None:
            return None

    ctx = _ctx(wiki_sources=_EmptySources(), wiki_cite_sources=True)
    out = await read_source_impl(ctx, "nope.md")
    assert out == "error: source not found: nope.md"


# ── tools.py: _read_step_names loop branches ─────────────────────────


def test_read_step_names_skips_short_rows_and_empty_cells():
    """branch 483->482: in the CSV-column path, a row shorter than the column
    index (or with an empty cell there) is skipped — the loop continues."""
    text = "wafer,step_name\nW1,STI_grow\nW2\nW3,\nW4,Gate_pvd\n"
    #         header        ok          short  empty   ok
    assert _read_step_names(text, "step_name") == ["STI_grow", "Gate_pvd"]


def test_read_step_names_skips_blank_lines_in_plain_list():
    """branch 487->486: in the plain-line-list path (column absent), a blank
    line is skipped — the loop continues."""
    text = "STI_grow\n   \nGate_pvd\n"
    assert _read_step_names(text, "absent_column") == ["STI_grow", "Gate_pvd"]


# ── tools.py: _parse_module_json fallbacks ───────────────────────────


def test_parse_module_json_unknown_on_invalid_json_inside_braces():
    """lines 504-505: the outermost {...} is found but isn't valid JSON →
    ('unknown', ''). (The `not isinstance(obj, dict)` guard on 506-507 is
    unreachable — see the pragma there — because a `\\{.*\\}` match that parses
    is always a JSON object.)"""
    assert _parse_module_json("prefix {not: valid, json} suffix") == ("unknown", "")


# ── tools.py: infer_modules empty-steps error ────────────────────────


async def test_infer_modules_errors_when_no_step_names():
    """line 606: a file with no usable step names → an actionable error naming
    the column the tool looked for."""

    async def fake_run(purpose, payload, sink, origin):  # pragma: no cover — never called
        return "{}", []

    fs = MemoryFileStore()
    files = WorkspaceFiles(fs)
    inv = "ws-1"
    await files.create(inv, "wafer-history.csv", b"   \n  \n")  # only blanks
    ctx = AgentToolContext(
        filestore=fs,
        files=files,
        investigation_id=inv,
        run_subagent=fake_run,
    )
    out = await infer_modules_impl(RunContextWrapper(ctx), "wafer-history.csv", column="step_name")
    assert "no step names found" in out
    assert "step_name" in out

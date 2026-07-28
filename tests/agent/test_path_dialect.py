"""One dialect for workspace paths in everything a model READS (#549).

`rel_path` fixed the strings we compute at runtime. These two guards cover the
static text that reaches the same context window — a tool's description and a
profile's seeded markdown — because a hand-written `/entities/foo.md` example
teaches exactly what the listings stopped teaching, and nothing else would catch
it: it is prose, so no type checker or unit test touches it.
"""

import pathlib
import re

from workspace_app.agent import build_tools
from workspace_app.agent.tools import _IMPLS

APPS = pathlib.Path(__file__).resolve().parents[2] / "src" / "workspace_app" / "apps"

# A rooted path inside inline code — `/entities/foo.md`, ``/data/x.csv``.
ROOTED_IN_CODE = re.compile(r"`+(/[A-Za-z0-9_.-][^`]*)`+")

# A rooted workspace path in prose or a fenced tree: `/step2-data/x.csv`, or a
# bare `/step2-data/` directory opening a line. Anchored on a boundary character
# so a URL (`https://host/a.md`) can't match.
ROOTED_IN_MARKDOWN = re.compile(
    r"(?m)(?:^|[`\s(])"
    r"(/[A-Za-z0-9_][A-Za-z0-9_./{}*-]*\.(?:md|csv|json|py|png|pptx|txt|ipynb)"
    r"|^/[A-Za-z0-9_][A-Za-z0-9_./{}*-]*/)"
)


def test_no_tool_description_teaches_a_rooted_workspace_path():
    """Every built-in tool, not just the workspace toolset — a description is
    part of the prompt, and an example path in it is an instruction."""
    offenders = [
        (tool.name, m.group(1))
        for tool in build_tools(sorted(_IMPLS))
        for m in ROOTED_IN_CODE.finditer(tool.description or "")
    ]
    assert offenders == []


def test_no_seeded_profile_markdown_teaches_a_rooted_workspace_path():
    """Profile prompts and SOPs are seeded INTO the workspace and read by the
    agent working there — the same context as a tool description."""
    offenders = [
        (md.relative_to(APPS).as_posix(), m.group(1))
        for md in sorted(APPS.rglob("*.md"))
        for m in ROOTED_IN_MARKDOWN.finditer(md.read_text(encoding="utf-8"))
    ]
    assert offenders == []

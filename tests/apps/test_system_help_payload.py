"""docs/plan-ai-reads-docs.md P3 — the docs reach the skill, and the image.

The skill's `docs` and `mkdocs.yml` are links to the repo's own, so there is one
copy of the docs. Two things have to hold for the AI to read them:

- the skill's payload, read through the links, is exactly what `docs/` holds (a
  parity test, the oracle being `docs/` listed now -- never a hand-kept list);
- the image carries `docs/` and `mkdocs.yml` where the links point, and the build
  context lets them in (`.dockerignore` used to exclude `docs`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import workspace_app.apps.shared_skills as shared
from workspace_app.apps.skill_payload import skill_payload

REPO = Path(shared.__file__).resolve().parents[3]
FOLDER = shared.SHARED_SKILLS_DIR / "system-help"


def test_the_payload_is_exactly_the_skill_and_the_docs():
    docs = REPO / "docs"
    expected = {f"docs/{p.relative_to(docs).as_posix()}" for p in docs.rglob("*") if p.is_file()}
    expected |= {"SKILL.md", "references/docs-reader.md", "mkdocs.yml"}

    payload = skill_payload(FOLDER)

    assert set(payload) == expected
    assert payload["mkdocs.yml"] == (REPO / "mkdocs.yml").read_bytes()
    assert payload["docs/design-history.md"] == (docs / "design-history.md").read_bytes()


# --- the image ---------------------------------------------------------------


def _rule(pattern: str) -> re.Pattern[str]:
    """A `.dockerignore` pattern as Docker reads it: `**` any number of path
    segments, `*` and `?` within one segment."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _in_context(path: str) -> bool:
    """Whether the build context carries `path`: the last matching rule wins, a
    `!` rule lets back in, and a rule matching a parent directory covers the
    files beneath it."""
    rules = []
    for line in (REPO / ".dockerignore").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keep = line.startswith("!")
        rules.append((keep, _rule(line.lstrip("!").strip("/"))))
    parts = path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    included = True
    for keep, rx in rules:
        if any(rx.match(c) for c in candidates):
            included = keep
    return included


@pytest.mark.parametrize(
    ("path", "carried"),
    [
        ("README.md", True),
        ("CLAUDE.md", False),
        ("web/node_modules/x/index.js", False),
        ("src/workspace_app/__init__.py", True),
    ],
)
def test_the_context_reader_reads_dockerignore_as_docker_does(path: str, carried: bool):
    """The positive control: the reader agrees with rules the image already
    relies on, or a green below would prove nothing."""
    assert _in_context(path) is carried


@pytest.mark.parametrize(
    "path",
    [
        "docs/index.md",
        "docs/design-history.md",
        "docs/subsystems/workflow-engine.md",
        "docs/workflows-syntax.html",
        "mkdocs.yml",
        "sample-skills/system-help/SKILL.md",
        "sample-skills/system-help/references/docs-reader.md",
    ],
)
def test_the_build_context_carries_the_docs(path: str):
    assert _in_context(path)


def test_the_image_puts_the_docs_where_the_links_point():
    """`sample-skills/system-help/docs -> ../../docs`: with the skills at
    `/app/sample-skills`, the docs must be at `/app/docs` and the nav at
    `/app/mkdocs.yml` (the Dockerfile's WORKDIR is /app)."""
    dockerfile = (REPO / "docker" / "Dockerfile").read_text()
    assert (FOLDER / "docs").resolve() == REPO / "docs"
    assert (FOLDER / "mkdocs.yml").resolve() == REPO / "mkdocs.yml"
    assert "COPY docs/ ./docs/" in dockerfile
    assert "COPY mkdocs.yml ./mkdocs.yml" in dockerfile
    assert "COPY sample-skills/ ./sample-skills/" in dockerfile

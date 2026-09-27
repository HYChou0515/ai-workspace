"""docs/plan-ai-reads-docs.md P5 — a doc the index does not name is a doc the
in-app AI never reads.

The AI's `docs-reader` reads an index first (the mkdocs `nav` for the current
docs, `design-history.md` for the plans) and then only the files it picks. So:

- every history doc (what the site's own `not_in_nav` sets apart: plans,
  handoffs, questions, early briefs) is in `design-history.md`;
- every other doc is in the `nav`;
- an overturned plan's first line under its title names the plan that overturned
  it, in one format, pointing at a file that exists.

Each list comes from `docs/` and the site config, never from the index it
checks.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
HISTORY = re.compile(r"(plan|handoff|q)-")
#: `> 被 #861（plan-marking-tuples.md）推翻`
MARKER = re.compile(r"^> 被 #(\d+)（((?:plan|handoff|q)-[\w-]+\.md)）推翻$")


def _plans() -> list[Path]:
    return sorted(p for p in DOCS.glob("*.md") if HISTORY.match(p.name))


def _not_in_nav() -> list[str]:
    """The site's own list of pages kept out of the nav (`/plan-*.md`, ...)."""
    text = (ROOT / "mkdocs.yml").read_text()
    block = text[text.index("\nnot_in_nav: |\n") :].split("\n")[2:]
    out = []
    for line in block:
        if not line.startswith("  "):
            break
        out.append(line.strip().lstrip("/"))
    return out


def _pages() -> set[str]:
    return {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*") if p.suffix in (".md", ".html")}


def _history() -> set[str]:
    """The pages `not_in_nav` sets apart, less README.md (not a page: excluded)."""
    rules = _not_in_nav()
    return {
        page
        for page in _pages()
        if page != "README.md" and any(fnmatch.fnmatchcase(page, r) for r in rules)
    }


def test_no_plan_lives_outside_docs():
    """A plan at the repo root is in no index and not in the skill's `docs` link:
    six were, until docs/plan-ai-reads-docs.md P4 moved them in."""
    assert sorted(p.name for p in ROOT.glob("*.md") if HISTORY.match(p.name)) == []


def test_every_history_doc_is_in_design_history():
    index = (DOCS / "design-history.md").read_text()
    linked = set(re.findall(r"\]\(([\w\-/]+\.(?:md|html))\)", index))
    assert _plans(), "no plans found"
    assert {p.name for p in _plans()} <= _history()
    assert sorted(_history() - linked) == []


def _nav_files() -> set[str]:
    text = (ROOT / "mkdocs.yml").read_text()
    nav = text[text.index("\nnav:") :]
    nav = nav[: nav.index("\nnot_in_nav:")]
    return set(re.findall(r":\s*([\w\-/]+\.(?:md|html))\s*$", nav, re.M))


def test_every_other_doc_is_in_the_nav():
    """README.md is excluded from the site (`exclude_docs`); images and data
    files are not pages."""
    pages = _pages() - _history() - {"README.md"}
    assert pages, "no docs found"
    assert sorted(pages - _nav_files()) == []


def test_nav_omitted_files_is_warn_so_strict_fails_a_page_left_out():
    """With `nav.omitted_files: info` a page left out of the nav built under
    `--strict` with rc 0 (probed 2026-09-27); `warn` makes `--strict` fail it."""
    text = (ROOT / "mkdocs.yml").read_text()
    block = text[text.index("\nvalidation:") :]
    nav = block[block.index("  nav:") :]
    assert re.search(r"^    omitted_files: warn$", nav, re.M)


def _markers(path: Path) -> tuple[list[str], list[str]]:
    """(the overturned-by lines, and those that are out of place): a marker
    belongs right under the title, before anything else."""
    lines = path.read_text().splitlines()
    found = [line for line in lines if line.startswith("> 被 ")]
    head = 0
    while head < len(lines) and not lines[head].startswith("# "):
        head += 1
    head += 1
    while head < len(lines) and (not lines[head].strip() or lines[head].startswith("> 被 ")):
        head += 1
    misplaced = [line for line in lines[head:] if line.startswith("> 被 ")]
    return found, misplaced


def test_every_overturned_by_line_has_the_one_format_and_names_a_plan_that_exists():
    bad: dict[str, list[str]] = {}
    for plan in _plans():
        found, misplaced = _markers(plan)
        for line in found:
            m = MARKER.match(line)
            if m is None:
                bad.setdefault(plan.name, []).append(f"format: {line}")
            elif m.group(2) == plan.name or not (DOCS / m.group(2)).is_file():
                bad.setdefault(plan.name, []).append(f"names no other plan: {line}")
        for line in misplaced:
            bad.setdefault(plan.name, []).append(f"not under the title: {line}")
    assert bad == {}

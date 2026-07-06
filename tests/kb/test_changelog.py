"""#441: parse the Keep a Changelog markdown (git-cliff output) into structured
releases for the web /help/releases view. Pure function — no I/O."""

from __future__ import annotations

from workspace_app.kb.changelog import parse_changelog


def test_parses_a_single_release_with_one_group_and_item():
    md = """# 更新紀錄 / Release notes

some header prose

---
## [2026.07.06] — 2026-07-06

### Added

- did a thing
"""
    releases = parse_changelog(md)

    assert len(releases) == 1
    r = releases[0]
    assert r.version == "2026.07.06"
    assert r.date == "2026-07-06"
    assert r.unreleased is False
    assert len(r.sections) == 1
    assert r.sections[0].group == "Added"
    assert r.sections[0].items == ["did a thing"]


def test_multiple_releases_newest_first_with_header_ignored():
    md = """# 更新紀錄 / Release notes

intro prose that must be ignored, even a stray [bracket].

---
## [2026.07.06] — 2026-07-06

### Added

- newer feature

### Fixed

- newer fix one
- newer fix two
## [2026.07.05] — 2026-07-05

### Performance

- older speedup
"""
    releases = parse_changelog(md)

    assert [r.version for r in releases] == ["2026.07.06", "2026.07.05"]
    newest = releases[0]
    assert [s.group for s in newest.sections] == ["Added", "Fixed"]
    assert newest.sections[1].items == ["newer fix one", "newer fix two"]
    assert releases[1].sections[0].group == "Performance"


def test_unreleased_section_is_flagged_and_dateless():
    md = "## [Unreleased]\n\n### Added\n\n- pending change\n"

    releases = parse_changelog(md)

    assert len(releases) == 1
    assert releases[0].version == "Unreleased"
    assert releases[0].unreleased is True
    assert releases[0].date is None


def test_non_iso_date_text_is_preserved():
    md = "## [0.1.0] — 初始版本 / Initial\n\n### Added\n\n- first\n"

    r = parse_changelog(md)[0]

    assert r.version == "0.1.0"
    assert r.date == "初始版本 / Initial"
    assert r.unreleased is False


def test_header_only_changelog_has_no_releases():
    md = "# 更新紀錄 / Release notes\n\nprose only\n\n---\n"

    assert parse_changelog(md) == []

"""#441: unit tests for the pure changelog renderer (PR-granularity, first-parent).

The git/IO glue lives in ``workspace_app.changelog.__main__`` (omitted from
coverage); everything here exercises the pure functions in ``.render``.
"""

from __future__ import annotations

from workspace_app.changelog.render import (
    CHANGELOG_HEADER,
    Commit,
    classify,
    effective_message,
    entries_for,
    parse_git_log,
    render_changelog,
    render_release,
    render_unreleased,
    version_to_date,
)


def _commit(subject: str = "", body: str = "", parents: int = 1) -> Commit:
    return Commit(parents=tuple(str(i) for i in range(parents)), subject=subject, body=body)


def _c(message: str) -> tuple[str, str]:
    """classify() narrowed to non-None for the positive cases."""
    result = classify(message)
    assert result is not None
    return result


# --- classify -------------------------------------------------------------


def test_classify_maps_each_type_to_its_group():
    assert _c("feat: a")[0] == "Added"
    assert _c("fix: a")[0] == "Fixed"
    assert _c("perf: a")[0] == "Performance"
    assert _c("refactor: a")[0] == "Changed"
    assert _c("docs: a")[0] == "Documentation"


def test_classify_renders_scope_as_a_trailing_parenthetical():
    group, text = _c("feat(#480): advertise disabled tools")
    assert group == "Added"
    assert text == "Advertise disabled tools (#480)"


def test_classify_without_scope_has_no_parenthetical():
    _, text = _c("fix: recover the answer")
    assert text == "Recover the answer"


def test_classify_upper_firsts_only_the_first_char():
    _, text = _c("feat: kb_search cap per turn")
    assert text == "Kb_search cap per turn"


def test_classify_preserves_non_ascii_descriptions():
    _, text = _c("fix(#501): 區分 sandbox filestore")
    assert text == "區分 sandbox filestore (#501)"


def test_classify_collapses_noisy_issue_refs_in_the_description():
    _, text = _c("feat: 自動 context card generation (feat #175)")
    assert text == "自動 context card generation (#175)"


def test_classify_tolerates_a_breaking_marker():
    group, text = _c("feat(api)!: drop the legacy route")
    assert group == "Added"
    assert text == "Drop the legacy route (api)"


def test_classify_drops_skipped_and_unknown_types():
    assert classify("chore: bump deps") is None
    assert classify("ci: fix runner") is None
    assert classify("test: add case") is None
    assert classify("style: format") is None
    assert classify("bump v2026.07.09") is None
    assert classify("wip whatever") is None
    assert classify("Merge pull request #1 from x") is None


# --- effective_message ----------------------------------------------------


def test_effective_message_uses_subject_for_a_plain_commit():
    assert effective_message(_commit(subject="feat: a thing")) == "feat: a thing"


def test_effective_message_promotes_pr_title_from_a_merge_body():
    merge = _commit(
        subject="Merge pull request #482 from HYChou0515/x",
        body="\nfeat(#480): advertise disabled tools\n",
        parents=2,
    )
    assert effective_message(merge) == "feat(#480): advertise disabled tools"


def test_effective_message_is_empty_for_a_bodyless_merge():
    assert effective_message(_commit(subject="Merge branch 'x'", parents=2)) == ""


# --- entries_for ----------------------------------------------------------


def test_entries_for_keeps_conventional_and_drops_the_rest():
    commits = [
        _commit(subject="feat: added thing"),
        _commit(subject="chore: noise"),
        _commit(subject="Merge pull request #9 from x", body="fix: real fix", parents=2),
    ]
    assert entries_for(commits) == [("Added", "Added thing"), ("Fixed", "Real fix")]


# --- version_to_date ------------------------------------------------------


def test_version_to_date_strips_v_and_takes_the_calver_date():
    assert version_to_date("v2026.07.09") == "2026-07-09"
    assert version_to_date("2026.07.09") == "2026-07-09"


def test_version_to_date_ignores_a_same_day_suffix():
    assert version_to_date("v2026.07.09.1") == "2026-07-09"


# --- rendering blocks -----------------------------------------------------


def test_render_release_orders_groups_and_spaces_them():
    section = render_release(
        "v2026.07.09",
        [("Fixed", "b"), ("Added", "a"), ("Documentation", "d")],
    )
    assert section == (
        "## [2026.07.09] — 2026-07-09\n"
        "\n### Added\n\n- a\n"
        "\n### Fixed\n\n- b\n"
        "\n### Documentation\n\n- d\n"
    )


def test_render_release_with_no_entries_is_just_the_header():
    assert render_release("v2026.07.09", []) == "## [2026.07.09] — 2026-07-09\n"


def test_render_unreleased_is_dateless():
    assert render_unreleased([]) == "## [Unreleased]\n"
    assert render_unreleased([("Added", "x")]) == "## [Unreleased]\n\n### Added\n\n- x\n"


# --- whole file -----------------------------------------------------------


def test_render_changelog_prefixes_the_header_and_blank_separates_sections():
    out = render_changelog(["## [A]\n", "## [B]\n"])
    assert out.startswith(CHANGELOG_HEADER)
    body = out[len(CHANGELOG_HEADER) :]
    # first section abuts the header's "---"; a blank line separates sections.
    assert body == "## [A]\n\n## [B]\n"


def test_render_changelog_with_no_sections_is_just_the_header():
    assert render_changelog([]) == CHANGELOG_HEADER


# --- parse_git_log --------------------------------------------------------


def test_parse_git_log_splits_records_and_fields_keeping_multiline_bodies():
    raw = (
        "sha1\x1fp1\x1ffeat: one\x1f\x1e"
        "\nsha2\x1fp1 p2\x1fMerge pull request #9 from x\x1ffix: two\nmore body\x1e"
    )
    commits = parse_git_log(raw)
    assert len(commits) == 2
    assert commits[0] == Commit(parents=("p1",), subject="feat: one", body="")
    assert commits[1].is_merge is True
    assert commits[1].parents == ("p1", "p2")
    assert commits[1].body == "fix: two\nmore body"


def test_parse_git_log_ignores_trailing_whitespace_records():
    assert parse_git_log("\x1e\n\x1e") == []

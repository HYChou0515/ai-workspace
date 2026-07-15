"""#441: render the platform CHANGELOG from git history — pure, no I/O.

The changelog is generated at **PR granularity**: one bullet per merged pull
request (or per commit landed straight on the release branch), *not* per commit.
The individual commits on a feature branch are intermediate steps ("P1 …",
"P2 …") and are noise in a user-facing changelog.

The caller (:mod:`workspace_app.changelog.__main__`) walks history with
``git log --first-parent`` and hands the resulting :class:`Commit` list here.
First-parent is the whole trick: it visits each PR-merge / squash / direct
commit exactly once and never descends into a merged branch, so the phase
commits simply never appear — no filtering heuristics needed. For a GitHub
"Merge pull request #N …" commit the PR title lives in the commit *body*, and
our PR titles are Conventional Commits, so :func:`effective_message` promotes
the body to be the message. Squash-merged and direct commits already carry a
Conventional subject and are used as-is.

The inverse (reading this markdown back into structured releases for the web
``/help/releases`` view) is :mod:`workspace_app.kb.changelog`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The bilingual page header, kept verbatim at the top of the generated file.
CHANGELOG_HEADER = """# 更新紀錄 / Release notes

本頁記錄平台面向使用者的重要更新,格式採 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)。
新版本區段由每個合併的 PR(Conventional Commits)自動生成,並隨程式碼一起進版控,所以每次部署
都會是最新的。請勿手改已生成的版本區段——它們會在下次 `make release` 時重新產生。

This page records notable user-facing platform updates in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. New version
sections are generated from the merged pull requests (Conventional Commits) and
ship with the code, so the running version is always current. Do not hand-edit
generated sections — they are regenerated on the next `make release`.

---
"""

# Conventional-commit type → Keep a Changelog group. The groups double as the
# machine-readable section headers the backend parser reads (kb/changelog.py →
# GET /help/releases); keep them the canonical English labels. The FE localises
# and splits default (Added / Fixed / Performance) from detailed (Changed /
# Documentation).
GROUP_MAP = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Performance",
    "refactor": "Changed",
    "docs": "Documentation",
}
# Emission order — user-facing groups first, detailed-only last. Not alphabetical.
GROUP_ORDER = ["Added", "Fixed", "Performance", "Changed", "Documentation"]
# Conventional types that never reach a user-facing changelog.
SKIP_TYPES = frozenset({"bump", "chore", "ci", "test", "style"})

# "feat(scope)!: description" — scope + breaking marker optional.
_CONVENTIONAL = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?!?:\s*(?P<desc>.+?)\s*$")
# Collapse "(feat #123)" / "(#123)" in the description to a clean "(#123)".
_ISSUE = re.compile(r"\((?:\w+\s)?#(\d+)\)")


@dataclass(frozen=True)
class Commit:
    """A single first-parent commit: its parent SHAs plus subject and body."""

    parents: tuple[str, ...]
    subject: str
    body: str

    @property
    def is_merge(self) -> bool:
        return len(self.parents) >= 2


def effective_message(commit: Commit) -> str:
    """The Conventional Commit line this commit contributes.

    A GitHub PR-merge commit's subject is "Merge pull request #N from …" and its
    body's first non-empty line is the PR title (a Conventional Commit); promote
    that. Every other commit contributes its own subject. A merge with an empty
    body yields "" (dropped downstream as non-conventional).
    """
    if commit.is_merge:
        for line in commit.body.splitlines():
            if line.strip():
                return line.strip()
        return ""
    return commit.subject.strip()


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def classify(message: str) -> tuple[str, str] | None:
    """Map a Conventional message to ``(group, bullet_text)``, or ``None`` to drop."""
    match = _CONVENTIONAL.match(message)
    if match is None:
        return None
    ctype = match.group("type").lower()
    if ctype in SKIP_TYPES or ctype not in GROUP_MAP:
        return None
    desc = _ISSUE.sub(r"(#\1)", match.group("desc"))
    scope = match.group("scope")
    text = _upper_first(desc) + (f" ({scope})" if scope else "")
    return GROUP_MAP[ctype], text


def entries_for(commits: list[Commit]) -> list[tuple[str, str]]:
    """The ``(group, bullet_text)`` entries for a range of first-parent commits."""
    entries: list[tuple[str, str]] = []
    for commit in commits:
        classified = classify(effective_message(commit))
        if classified is not None:
            entries.append(classified)
    return entries


def version_to_date(version: str) -> str:
    """CalVer ``v2026.07.09`` / ``2026.07.09.1`` → ISO date ``2026-07-09``.

    The date is derived from the version itself, so it can never drift from the
    version across timezones (unlike a wall-clock ``now()``).
    """
    parts = version.lstrip("v").split(".")
    return "-".join(parts[:3])


def _render_block(header_line: str, entries: list[tuple[str, str]]) -> str:
    lines = [header_line]
    for group in GROUP_ORDER:
        items = [text for grp, text in entries if grp == group]
        if not items:
            continue
        lines.append("")
        lines.append(f"### {group}")
        lines.append("")
        lines.extend(f"- {text}" for text in items)
    return "\n".join(lines) + "\n"


def render_release(version: str, entries: list[tuple[str, str]]) -> str:
    """A ``## [version] — date`` section (date derived from the CalVer version)."""
    label = version.lstrip("v")
    return _render_block(f"## [{label}] — {version_to_date(version)}", entries)


def render_unreleased(entries: list[tuple[str, str]]) -> str:
    """The dateless ``## [Unreleased]`` section (never shown on the web)."""
    return _render_block("## [Unreleased]", entries)


def render_changelog(sections: list[str]) -> str:
    """Header + sections, newest first. One blank line between sections."""
    out = CHANGELOG_HEADER
    for index, section in enumerate(sections):
        out += ("\n" if index else "") + section
    return out


def parse_git_log(raw: str) -> list[Commit]:
    """Parse ``git log --format=%H%x1f%P%x1f%s%x1f%b%x1e`` output into commits.

    Records are separated by RS (``\\x1e``) and fields by US (``\\x1f``) so a
    body spanning newlines stays intact.
    """
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        _sha, parents, subject, body = record.split("\x1f")
        commits.append(Commit(parents=tuple(parents.split()), subject=subject, body=body))
    return commits

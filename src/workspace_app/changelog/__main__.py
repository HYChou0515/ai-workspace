"""#441: CLI that walks git history and writes the packaged CHANGELOG.md.

`python -m workspace_app.changelog` — thin git/IO glue around
:mod:`workspace_app.changelog.render`. All the logic worth testing lives in
render.py; this module only shells out to ``git`` and writes a file, so it is
excluded from coverage (see pyproject `[tool.coverage.run] omit`).

    --unreleased            print the section for commits since the newest tag
                            (preview; writes nothing)
    --release-version vX    render those commits under [vX] instead of
                            [Unreleased] (used by `make release`, before the tag
                            exists) and write the whole file
    --write                 regenerate the whole packaged CHANGELOG.md in place

With no flags it prints the whole changelog to stdout (a dry run).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from workspace_app.changelog.render import (
    entries_for,
    parse_git_log,
    render_changelog,
    render_release,
    render_unreleased,
)

# RS-terminated records, US-separated fields — a body with newlines stays whole.
_LOG_FORMAT = "%H%x1f%P%x1f%s%x1f%b%x1e"
_CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "help_content" / "CHANGELOG.md"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def _tags() -> list[str]:
    """CalVer / legacy ``v[0-9]*`` tags, oldest first (numeric-aware sort)."""
    tags = [t for t in _git("tag", "--list").split() if re.match(r"^v[0-9]", t)]
    return sorted(tags, key=lambda t: [int(n) for n in re.findall(r"\d+", t)])


def _commits(rev_range: str) -> list:
    return parse_git_log(
        _git("log", "--first-parent", "--reverse", f"--format={_LOG_FORMAT}", rev_range)
    )


def build(release_version: str | None) -> str:
    """The whole changelog markdown, newest release first."""
    tags = _tags()
    newest = tags[-1] if tags else None
    post = _commits(f"{newest}..HEAD") if newest else _commits("HEAD")
    post_entries = entries_for(post)

    sections: list[str] = []
    if release_version:
        sections.append(render_release(release_version, post_entries))
    elif post_entries:
        sections.append(render_unreleased(post_entries))

    for i in range(len(tags) - 1, -1, -1):
        rev = f"{tags[i - 1]}..{tags[i]}" if i > 0 else tags[i]
        sections.append(render_release(tags[i], entries_for(_commits(rev))))

    return render_changelog(sections)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m workspace_app.changelog")
    parser.add_argument("--unreleased", action="store_true")
    parser.add_argument("--release-version", default=None)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    if args.unreleased:
        newest = _tags()[-1] if _tags() else None
        post = _commits(f"{newest}..HEAD") if newest else _commits("HEAD")
        sys.stdout.write(render_unreleased(entries_for(post)))
        return

    text = build(args.release_version)
    if args.write:
        _CHANGELOG_PATH.write_text(text, encoding="utf-8")
        sys.stderr.write(f"wrote {_CHANGELOG_PATH}\n")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()

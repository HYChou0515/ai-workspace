"""YAML-frontmatter parsing for the markdown artefacts an App ships or a user
writes in a workspace — skills (``.skill/<name>/SKILL.md``) and sub-agents
(``.agent/<name>/AGENT.md``).

Extracted from ``apps/skills.py`` when the sub-agent loader needed the same
split: two copies of a frontmatter parser drift, and the two artefact kinds
must agree on what "well-formed frontmatter" means or a user who learned one
format gets surprised by the other.
"""

from __future__ import annotations


class FrontmatterError(Exception):
    """The `---` block couldn't be parsed. Callers translate it into their own
    artefact-flavoured error (``SkillError`` / ``SubagentError``) so the tool
    that surfaces it can keep saying "skill" or "sub-agent" to the agent."""


def parse_frontmatter(raw: bytes) -> tuple[dict[str, object], str]:
    """Split an `---` ... `---` YAML frontmatter from its body. Returns
    `({}, raw_decoded)` when no frontmatter is present. Raises
    `FrontmatterError` on malformed YAML."""
    text = raw.decode("utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    rest = text[3:].lstrip("\n")
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    front_text = rest[:end]
    body_text = rest[end + 4 :].lstrip("\n")
    try:
        front = _parse_yaml(front_text)
    except ValueError as e:
        raise FrontmatterError(f"malformed frontmatter YAML: {e}") from e
    if not isinstance(front, dict):  # pragma: no cover — _parse_yaml only returns dict
        raise FrontmatterError(f"frontmatter must be a YAML mapping, got {type(front).__name__}")
    return {str(k): v for k, v in front.items()}, body_text


def _parse_yaml(text: str) -> object:
    """Minimal YAML loader: `name: value` lines with `#` comments + blank lines
    tolerated. Avoids PyYAML — the frontmatter is tiny (name + description)."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"line without `:`: {raw_line!r}")
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith(("[", "{")) and not _balanced(value):
            raise ValueError(f"unbalanced delimiter in value: {value!r}")
        out[key.strip()] = value
    return out


def _balanced(s: str) -> bool:
    """Cheap `[]` / `{}` open-close balance check — flags `description: [unclosed`."""
    depth = 0
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0

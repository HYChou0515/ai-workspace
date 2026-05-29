"""Skills — progressive disclosure for the RCA agent (issue #29).

A skill is a markdown file under a template profile's `.skill/<name>/SKILL.md`
folder, carrying YAML frontmatter:

    ---
    name: 5-why-walkthrough
    description: 引導用戶完成 5 Whys 流程。當用戶說「為什麼」/「root cause」時使用。
    ---

    (body markdown — what gets injected into the agent's context when it
    calls `read_skill(name)`.)

The host:
- Lists `(name, description)` in the system prompt at every turn, so the
  agent knows which skills are available + when each applies.
- Reads the body on demand via the `read_skill` tool — progressive
  disclosure: large bodies don't bloat every turn.

See `docs/plan-skills-and-tools.md` §A.
"""

from __future__ import annotations

import logging
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable

import msgspec

logger = logging.getLogger(__name__)

# Module-level constant for the templates resource package — tests
# monkeypatch this to point at a synthetic package for isolation.
_TEMPLATES_PKG = "workspace_app.rca.templates"

# Per-skill body hard cap. A methodology over this size should be split
# into multiple skills; truncating would silently drop steps, which is
# worse than failing loud.
SKILL_BODY_CAP = 50_000


class SkillError(Exception):
    """The skill subsystem couldn't satisfy a request — unknown name,
    body too large, frontmatter unparseable. The `read_skill` tool
    catches this and surfaces a friendly error string to the agent."""


class SkillMeta(msgspec.Struct, frozen=True):
    """What the agent sees in the system-prompt index: a name to call
    `read_skill(name)` with + a one-line description that doubles as
    "when to use" (Anthropic Skills convention)."""

    name: str
    description: str


@cache
def list_skills(profile: str) -> list[SkillMeta]:
    """Return the skill list for a template `profile`, sorted by name.

    Unknown profile / profile without a `.skill/` dir → empty list (no
    error: a profile may simply have no skills, and the system-prompt
    index gets skipped in that case)."""
    skill_root = _skill_root(profile)
    if skill_root is None:
        return []
    out: list[SkillMeta] = []
    for sub in sorted(skill_root.iterdir(), key=lambda t: t.name):
        if not sub.is_dir():
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            raw = skill_md.read_bytes()
            front, _body = _parse_frontmatter(raw)
        except SkillError as e:
            logger.warning("skill %r in %r: %s — skipping", sub.name, profile, e)
            continue
        name = str(front.get("name", "")).strip()
        description = str(front.get("description", "")).strip()
        if not name:
            logger.warning(
                "skill %r in %r: frontmatter missing `name` — skipping",
                sub.name,
                profile,
            )
            continue
        if name != sub.name:
            logger.warning(
                "skill %r in %r: frontmatter name=%r mismatches dir name — skipping",
                sub.name,
                profile,
                name,
            )
            continue
        out.append(SkillMeta(name=name, description=description))
    return out


@cache
def load_skill(profile: str, name: str) -> str:
    """Return a skill's body markdown (frontmatter stripped).

    Raises `SkillError` on unknown name or body cap exceeded — the
    `read_skill` tool catches it and surfaces a friendly correction to
    the agent."""
    skill_root = _skill_root(profile)
    if skill_root is None:
        raise SkillError(f"profile {profile!r} has no skills")
    target = skill_root / name / "SKILL.md"
    if not target.is_file():
        avail = ", ".join(m.name for m in list_skills(profile)) or "(none)"
        raise SkillError(f"unknown skill {name!r} in profile {profile!r}. available: {avail}")
    _front, body = _parse_frontmatter(target.read_bytes())
    if len(body) > SKILL_BODY_CAP:
        raise SkillError(
            f"skill {name!r} body exceeds {SKILL_BODY_CAP} chars "
            f"({len(body)}); please split it into smaller skills"
        )
    return body


# ─── internals ───────────────────────────────────────────────────────


def _skill_root(profile: str) -> Traversable | None:
    """The `<profile>/.skill/` traversable, or None if the profile or
    skill dir doesn't exist. We use `Traversable` (not raw Path) so this
    works the same way for editable installs and zip-imported wheels."""
    try:
        pkg = resources.files(_TEMPLATES_PKG)
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    profile_root = pkg / profile
    skill_root = profile_root / ".skill"
    try:
        if not skill_root.is_dir():
            return None
    except (FileNotFoundError, NotADirectoryError):
        return None
    return skill_root


def _parse_frontmatter(raw: bytes) -> tuple[dict[str, str], str]:
    """Split an `---` ... `---` YAML frontmatter from its body.

    Returns `({}, raw_decoded)` when no frontmatter is present (an
    author may write a body-only `SKILL.md`; `list_skills` will then
    drop it for missing `name`). Raises `SkillError` on malformed YAML."""
    text = raw.decode("utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    # Find the closing fence — `\n---\n` or `\n---` at EOF.
    rest = text[3:].lstrip("\n")
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    front_text = rest[:end]
    body_text = rest[end + 4 :].lstrip("\n")
    try:
        front = _parse_yaml(front_text)
    except ValueError as e:
        raise SkillError(f"malformed frontmatter YAML: {e}") from e
    if not isinstance(front, dict):
        raise SkillError(f"frontmatter must be a YAML mapping, got {type(front).__name__}")
    return {str(k): v for k, v in front.items()}, body_text


def _parse_yaml(text: str) -> object:
    """Minimal YAML loader: `name: value` lines with `#` comments and
    blank lines tolerated. We deliberately avoid pulling PyYAML — the
    frontmatter shape is tiny (just `name` + `description`)."""
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"line without `:`: {raw_line!r}")
        key, _, value = line.partition(":")
        value = value.strip()
        # Reject obvious list/dict literals — the frontmatter contract
        # is scalar key:value only.
        if value.startswith(("[", "{")) and not _balanced(value):
            raise ValueError(f"unbalanced delimiter in value: {value!r}")
        out[key.strip()] = value
    return out


def _balanced(s: str) -> bool:
    """Cheap check: count `[]` / `{}` open-close — used to flag the
    common malformed-frontmatter case `description: [unclosed`."""
    depth = 0
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0

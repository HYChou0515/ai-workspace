"""Skills — progressive disclosure for an App's agent (issue #29, #89).

A skill is a markdown file under a profile's ``.skill/<name>/SKILL.md`` folder
(``apps/<slug>/profiles/<profile>/.skill/``), carrying YAML frontmatter:

    ---
    name: report-format
    description: How to structure the final RCA report. Use before drafting one.
    ---

    (body markdown — injected into the agent's context when it calls
    `read_skill(name)`.)

The host lists ``(name, description)`` in the system prompt each turn (so the
agent knows which skills apply) and reads the body on demand via the
``read_skill`` tool. See ``docs/plan-skills-and-tools.md`` §A.
"""

from __future__ import annotations

import logging
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable

import msgspec

logger = logging.getLogger(__name__)

_APPS_PKG = "workspace_app.apps"
_PROFILES_DIR = "profiles"

# Per-skill body hard cap. A methodology over this size should be split into
# multiple skills; truncating would silently drop steps — worse than failing.
SKILL_BODY_CAP = 50_000


class SkillError(Exception):
    """The skill subsystem couldn't satisfy a request — unknown name, body too
    large, frontmatter unparseable. The `read_skill` tool catches this and
    surfaces a friendly error string to the agent."""


class SkillMeta(msgspec.Struct, frozen=True):
    """What the agent sees in the system-prompt index: a name to call
    `read_skill(name)` with + a one-line "when to use" description."""

    name: str
    description: str


@cache
def list_skills(app_slug: str, profile: str) -> list[SkillMeta]:
    """The skill list for an App's `profile`, sorted by name. Unknown profile /
    no `.skill/` dir → empty list (a profile may simply ship none; the
    system-prompt index is then skipped)."""
    skill_root = _skill_root(app_slug, profile)
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
            front, _body = _parse_frontmatter(skill_md.read_bytes())
        except SkillError as e:
            logger.warning("skill %r in %r/%r: %s — skipping", sub.name, app_slug, profile, e)
            continue
        name = str(front.get("name", "")).strip()
        description = str(front.get("description", "")).strip()
        if not name:
            logger.warning(
                "skill %r in %r/%r: missing `name` — skipping", sub.name, app_slug, profile
            )
            continue
        if name != sub.name:
            logger.warning(
                "skill %r in %r/%r: frontmatter name=%r mismatches dir — skipping",
                sub.name,
                app_slug,
                profile,
                name,
            )
            continue
        out.append(SkillMeta(name=name, description=description))
    return out


@cache
def load_skill(app_slug: str, profile: str, name: str) -> str:
    """A skill's body markdown (frontmatter stripped). Raises `SkillError` on
    unknown name or body cap exceeded — `read_skill` catches it."""
    skill_root = _skill_root(app_slug, profile)
    if skill_root is None:
        raise SkillError(f"profile {profile!r} has no skills")
    target = skill_root / name / "SKILL.md"
    if not target.is_file():
        avail = ", ".join(m.name for m in list_skills(app_slug, profile)) or "(none)"
        raise SkillError(f"unknown skill {name!r} in profile {profile!r}. available: {avail}")
    _front, body = _parse_frontmatter(target.read_bytes())
    if len(body) > SKILL_BODY_CAP:
        raise SkillError(
            f"skill {name!r} body exceeds {SKILL_BODY_CAP} chars "
            f"({len(body)}); please split it into smaller skills"
        )
    return body


# ─── internals ───────────────────────────────────────────────────────


def _skill_root(app_slug: str, profile: str) -> Traversable | None:
    """The `apps/<slug>/profiles/<profile>/.skill/` traversable, or None if it
    doesn't exist. `Traversable` (not raw Path) so it works for editable
    installs and zip-imported wheels alike."""
    try:
        pkg = resources.files(_APPS_PKG)
    except (ModuleNotFoundError, FileNotFoundError):  # pragma: no cover — defensive
        return None
    skill_root = pkg / app_slug / _PROFILES_DIR / profile / ".skill"
    try:
        if not skill_root.is_dir():
            return None
    except (FileNotFoundError, NotADirectoryError):  # pragma: no cover — Traversable shim
        return None
    return skill_root


def _parse_frontmatter(raw: bytes) -> tuple[dict[str, object], str]:
    """Split an `---` ... `---` YAML frontmatter from its body. Returns
    `({}, raw_decoded)` when no frontmatter is present. Raises `SkillError` on
    malformed YAML."""
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
        raise SkillError(f"malformed frontmatter YAML: {e}") from e
    if not isinstance(front, dict):  # pragma: no cover — _parse_yaml only returns dict
        raise SkillError(f"frontmatter must be a YAML mapping, got {type(front).__name__}")
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

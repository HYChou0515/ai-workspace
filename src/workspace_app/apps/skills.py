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
import re
from collections.abc import Mapping
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import TYPE_CHECKING

import msgspec

if TYPE_CHECKING:
    from ..files import WorkspaceFiles

logger = logging.getLogger(__name__)

_APPS_PKG = "workspace_app.apps"
_PROFILES_DIR = "profiles"

# Where a user+AI co-created skill lives in a workspace (#298). A folder under
# the workspace root, mirroring the package `.skill/` layout; the body is read
# live every turn (NOT @cache — the workspace is hand-editable + just-written).
WORKSPACE_SKILL_DIR = ".skill"

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


# ─── workspace skills (#298) ─────────────────────────────────────────


def _enforce_cap(name: str, body: str) -> str:
    if len(body) > SKILL_BODY_CAP:
        raise SkillError(
            f"skill {name!r} body exceeds {SKILL_BODY_CAP} chars "
            f"({len(body)}); please split it into smaller skills"
        )
    return body


async def load_workspace_skill(files: WorkspaceFiles, workspace_id: str, name: str) -> str | None:
    """Body markdown of a co-created skill at ``<workspace>/.skill/<name>/SKILL.md``
    (frontmatter stripped), or ``None`` when it doesn't exist. Read live (never
    cached) since the workspace is hand-editable + may have just been written this
    turn (#298 Q3a). Raises ``SkillError`` only on body-cap exceeded."""
    from ..filestore.protocol import FileNotFound

    path = f"/{WORKSPACE_SKILL_DIR}/{name}/SKILL.md"
    try:
        raw = await files.read(workspace_id, path)
    except FileNotFound:
        return None
    _front, body = _parse_frontmatter(raw)
    return _enforce_cap(name, body)


async def workspace_skill_metas(files: WorkspaceFiles, workspace_id: str) -> list[SkillMeta]:
    """``(name, description)`` for every well-formed skill under the workspace's
    ``.skill/`` dir, sorted by name. Unparseable / name-mismatched / nameless
    skills are skipped (logged) — same tolerance as the package loader, so one
    bad hand-edit can't break the whole index. Empty when there's no ``.skill/``."""
    prefix = f"/{WORKSPACE_SKILL_DIR}/"
    paths = await files.ls(workspace_id, prefix)
    out: list[SkillMeta] = []
    for path in sorted(paths):
        rel = path[len(prefix) :]
        if rel.count("/") != 1 or not rel.endswith("/SKILL.md"):
            continue
        dir_name = rel[: -len("/SKILL.md")]
        meta = await _workspace_skill_meta(files, workspace_id, path, dir_name)
        if meta is not None:
            out.append(meta)
    return out


async def _workspace_skill_meta(
    files: WorkspaceFiles, workspace_id: str, path: str, dir_name: str
) -> SkillMeta | None:
    raw = await files.read(workspace_id, path)
    try:
        front, _body = _parse_frontmatter(raw)
    except SkillError as e:
        logger.warning("workspace skill %r: %s — skipping", dir_name, e)
        return None
    name = str(front.get("name", "")).strip()
    description = str(front.get("description", "")).strip()
    if not name:
        logger.warning("workspace skill %r: missing `name` — skipping", dir_name)
        return None
    if name != dir_name:
        logger.warning(
            "workspace skill %r: frontmatter name=%r mismatches dir — skipping", dir_name, name
        )
        return None
    return SkillMeta(name=name, description=description)


def workspace_skills_block(metas: list[SkillMeta]) -> str:
    """Render the per-turn "skills you created in this workspace" index, or ``""``
    when there are none. Injected fresh each turn (like context_files) so a skill
    the agent just saved is advertised next turn (#298 Q3a)."""
    if not metas:
        return ""
    lines = [
        "## Skills in this workspace",
        "",
        "You (with the user) created these. Call `read_skill(name)` to load one "
        "before applying it.",
        "",
    ]
    lines += [f"- `{m.name}`: {m.description}" for m in metas]
    return "\n".join(lines)


async def build_workspace_skills_block(
    files: WorkspaceFiles, workspace_id: str, prefs: Mapping[str, bool] | None = None
) -> str:
    """Read the workspace's `.skill/` live and render the index block (or ``""``).
    A workspace skill the item toggled OFF (`prefs[name]` is False, #380) is
    dropped — the agent isn't told about a skill the user turned off (workspace
    skills are default-on, so only an explicit False hides one)."""
    metas = await workspace_skill_metas(files, workspace_id)
    if prefs:
        metas = [m for m in metas if prefs.get(m.name) is not False]
    return workspace_skills_block(metas)


class SkillState(msgspec.Struct, frozen=True):
    """#380: one skill's per-item picker state — its ``source`` (``shared`` /
    ``profile`` / ``workspace``), the profile/App ``default_on`` before any
    override, and the ``effective`` result after the item's tri-state
    ``skill_prefs`` is applied. The API layer adds the ``follow``/``on``/``off``
    ``pref`` label from the raw prefs."""

    name: str
    description: str
    source: str
    default_on: bool
    effective: bool


def effective_item_skills(
    app_slug: str,
    profile: str,
    prefs: Mapping[str, bool],
    workspace_metas: list[SkillMeta],
) -> list[SkillState]:
    """The item's full skills picker state (#380), one row per available skill
    across all three sources — the App's declared shared skills, the profile's
    package ``.skill/`` skills, and the co-created workspace skills — sorted by
    name. Deduped with priority ``workspace > profile > shared`` (a workspace or
    package skill shadows a shared one of the same name, matching read_skill).

    ``default_on``: package + workspace skills are on by default; a shared skill
    is on only if the profile's ``skills`` opts it in (or the profile leaves
    ``skills`` unset → all declared shared are on). ``effective`` applies the
    per-item tri-state ``prefs`` on top (``True`` on, ``False`` off, absent →
    ``default_on``). Single source for the picker endpoint AND the turn's prompt
    index (``AppCatalog.resolve``), so the two can't drift."""
    from msgspec import UNSET

    from .manifest import load_app_manifest
    from .profiles import load_profile
    from .shared_skills import shared_skill_metas

    declared = list(load_app_manifest(app_slug).agent.skills)
    prof_skills = load_profile(app_slug, profile).skills
    default_shared = set(declared if prof_skills is UNSET else prof_skills)
    # name → (meta, source, default_on); later writes shadow earlier ones.
    rows: dict[str, tuple[SkillMeta, str, bool]] = {}
    for m in shared_skill_metas(declared):
        rows[m.name] = (m, "shared", m.name in default_shared)
    for m in list_skills(app_slug, profile):
        rows[m.name] = (m, "profile", True)
    for m in workspace_metas:
        rows[m.name] = (m, "workspace", True)
    out: list[SkillState] = []
    for name in sorted(rows):
        meta, source, default_on = rows[name]
        pinned = prefs.get(name)
        effective = pinned if pinned is not None else default_on
        out.append(
            SkillState(
                name=name,
                description=meta.description,
                source=source,
                default_on=default_on,
                effective=effective,
            )
        )
    return out


async def resolve_skill_body(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
) -> str | None:
    """A skill's body across the three sources in read_skill's precedence —
    workspace ``.skill/`` first (the user's own shadows), then a shared registry
    skill, then the profile package skill. ``None`` when no source has it. Raises
    ``SkillError`` only on a body over the cap. #380: the apply-this-turn preload
    resolves the body IGNORING the enable/disable toggle (apply overrides off)."""
    from .shared_skills import SHARED_SKILLS, load_shared_skill

    body = await load_workspace_skill(files, workspace_id, name)
    if body is not None:
        return body
    if name in SHARED_SKILLS:
        return load_shared_skill(name)
    if app_slug is not None and profile is not None:
        try:
            return load_skill(app_slug, profile, name)
        except SkillError:
            return None
    return None


async def build_applied_skills_block(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    names: list[str],
) -> str:
    """Render the per-turn "apply these skills now" block (#380) — each named
    skill's full body under its own heading, preceded by an instruction to apply
    them this turn. A name whose body can't be resolved (unknown, or over the cap)
    is skipped with a short note so the turn still proceeds. ``""`` when nothing
    resolves. Injected like the workspace block: transient, never persisted."""
    sections: list[str] = []
    for name in names:
        try:
            body = await resolve_skill_body(files, workspace_id, app_slug, profile, name)
        except SkillError as e:
            sections.append(f"### {name}\n\n(could not load: {e})")
            continue
        if body is None:
            sections.append(f"### {name}\n\n(skill not found — skipped)")
        else:
            sections.append(f"### {name}\n\n{body}")
    if not sections:
        return ""
    header = (
        "## Apply these skills now\n\n"
        "The user selected the following skill(s) to apply THIS turn. Read them and "
        "follow them as you answer."
    )
    return "\n\n".join([header, *sections])


def merged_profile_skills(
    app_slug: str, profile: str, declared_shared: list[str]
) -> list[SkillMeta]:
    """The static skill index for a turn's system prompt: the App's declared
    shared skills (#298 Q7) + the profile's own package ``.skill/`` skills, deduped
    by name (package wins a clash), sorted. The user's *workspace* skills are added
    separately per turn (they need the live FileStore)."""
    from .shared_skills import shared_skill_metas

    metas: dict[str, SkillMeta] = {m.name: m for m in shared_skill_metas(declared_shared)}
    for m in list_skills(app_slug, profile):
        metas[m.name] = m
    return [metas[k] for k in sorted(metas)]


def slugify_skill_name(name: str) -> str:
    """A skill name → kebab-case slug (lowercase; non-alphanumeric runs become a
    single ``-``; trimmed). ``save_skill`` uses this so the frontmatter ``name``
    always equals the folder name and the loader never silently skips it. Returns
    ``""`` when nothing usable remains (caller rejects)."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def render_skill_md(slug: str, description: str, body: str) -> str:
    """Assemble a well-formed SKILL.md: minimal `name`+`description` frontmatter
    (#298 Q9) + body. ``description`` is collapsed to a single line because the
    frontmatter parser is line-based — a newline would truncate it."""
    desc = " ".join(description.split())
    return f"---\nname: {slug}\ndescription: {desc}\n---\n\n{body.strip()}\n"


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

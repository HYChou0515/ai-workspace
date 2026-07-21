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
from typing import TYPE_CHECKING, Any

import msgspec

from .skill_payload import ORIGIN_FILE, SkillSource, origin_for, skill_payload

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
    #: #589 — this workspace folder is a COPY of a baked-in skill (it carries an
    #: `.origin` manifest), not a skill written here. It still lives in the
    #: workspace and is still editable; it simply must not be mistaken for the
    #: user's own work when deciding source and default-on.
    is_copy: bool = False


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
    # Which folders are copies falls straight out of the listing we already have,
    # so knowing it costs no extra read.
    copies = {
        p[len(prefix) :].removesuffix(f"/{ORIGIN_FILE}")
        for p in paths
        if p.endswith(f"/{ORIGIN_FILE}")
    }
    out: list[SkillMeta] = []
    for path in sorted(paths):
        rel = path[len(prefix) :]
        if rel.count("/") != 1 or not rel.endswith("/SKILL.md"):
            continue
        dir_name = rel[: -len("/SKILL.md")]
        meta = await _workspace_skill_meta(files, workspace_id, path, dir_name)
        if meta is not None:
            out.append(msgspec.structs.replace(meta, is_copy=dir_name in copies))
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
    #: #589 — the workspace holds an editable COPY of this baked-in skill. Kept
    #: separate from ``source`` because the two facts are independent: the copy
    #: still answers as the skill it copied (so a default-off one can't be turned
    #: on for good just by using it), yet its files really are here — downloadable,
    #: editable, and refreshable from upstream.
    is_copy: bool = False


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
        prior = rows.get(m.name)
        if m.is_copy and prior is not None:
            # A copy of a baked-in skill answers as the skill it copied. Its
            # DESCRIPTION comes from the copy — that is the text actually read
            # this turn, and the AI may have edited it — but its source and
            # default-on stay the package's, so using a default-off skill once
            # cannot quietly turn it on for good.
            rows[m.name] = (m, prior[1], prior[2])
        else:
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
                is_copy=meta.is_copy,
            )
        )
    return out


def augment_shared_skill_body(
    name: str, body: str, app_slug: str | None, profile: str | None
) -> str:
    """Append machine-derived, always-current detail to a shared skill whose static body is
    deliberately purpose-only (plan §3). ``author-workflow`` gets the DSL grammar (derived
    from the schema — P5) + this app's capability/tool boundaries (P6), so the AI drafts
    against the real grammar and knows what it can/can't do, without the skill drifting."""
    if name != "author-workflow":
        return body
    from ..agent.tools import _profile_tool_ceiling
    from ..workflow.dsl import describe_dsl_grammar, describe_workflow_boundaries

    ceiling = _profile_tool_ceiling(app_slug, profile)
    return "\n\n".join([body, describe_dsl_grammar(), describe_workflow_boundaries(ceiling)])


def _skill_source(
    app_slug: str | None, profile: str | None, name: str
) -> tuple[SkillSource, Any] | None:
    """Where a baked-in skill's files live, or None if it isn't one. Precedence
    matches the body resolvers: a profile package skill shadows a shared one."""
    if app_slug is not None and profile is not None:
        root = _skill_root(app_slug, profile)
        if root is not None:
            candidate = root / name
            if (candidate / "SKILL.md").is_file():
                return ("profile", candidate)
    from .shared_skills import SHARED_SKILLS

    src = SHARED_SKILLS.get(name)
    return ("shared", src) if src is not None else None


async def materialize_skill(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
) -> None:
    """#589: copy a baked-in skill's files into the workspace so the body's own
    instructions resolve — ``see references/glossary.md`` and
    ``exec(["python", ".skill/<name>/scripts/x.py"])`` only work if the files are
    actually there.

    Copy-if-absent: a workspace copy already present is left completely alone.
    That is the whole point — the AI is meant to tweak these scripts, and an
    overwrite would delete its work. Refreshing a copy is a separate, explicit
    action, never a side effect of using the skill.

    Writes go through ``WorkspaceFiles``, which routes to the item's live sandbox
    when one is already awake (so the files are usable THIS turn) and to the
    durable store when it is cold — without waking it, which `read_skill`
    promises. A workspace over quota fails here, loudly, like any other write.
    """
    if await files.ls(workspace_id, f"/{WORKSPACE_SKILL_DIR}/{name}/"):
        return
    found = _skill_source(app_slug, profile, name)
    if found is None:
        return
    source, src_dir = found
    payload = skill_payload(src_dir)
    # A skill that is nothing but its SKILL.md has nothing to materialize, and
    # copying it anyway would be pure cost: the copy shadows the package version,
    # so the body stops tracking upstream and the skill starts reporting as a
    # workspace one. Every skill shipped today is exactly that shape, so the
    # common case must stay untouched — only a skill that actually brings files
    # becomes a local copy.
    if set(payload) <= {"SKILL.md"}:
        return
    for rel, data in payload.items():
        await files.write(workspace_id, f"/{WORKSPACE_SKILL_DIR}/{name}/{rel}", data)
    # Written LAST: until it exists the copy is incomplete, and a manifest that
    # outlived a half-written copy would claim shipped bytes for files that were
    # never written.
    await files.write(
        workspace_id,
        f"/{WORKSPACE_SKILL_DIR}/{name}/{ORIGIN_FILE}",
        msgspec.json.encode(origin_for(source, payload)),
    )


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

    await materialize_skill(files, workspace_id, app_slug, profile, name)
    body = await load_workspace_skill(files, workspace_id, name)
    if body is None and name in SHARED_SKILLS:
        body = load_shared_skill(name)
    if body is None and app_slug is not None and profile is not None:
        try:
            body = load_skill(app_slug, profile, name)
        except SkillError:
            return None
    if body is None:
        return None
    # #589: the derived reference is appended to whatever body we resolved, from
    # ANY source. It used to hang off the shared branch alone, which was fine
    # while a baked-in skill could never be copied into the workspace. Once it
    # can, the workspace copy wins the precedence above — and a source-specific
    # augmentation would silently stop firing, freezing the AI's idea of the
    # workflow syntax on the day the skill was copied. That staleness is the one
    # thing the derivation exists to prevent, so it cannot depend on provenance:
    # the AUTHORED text is what gets copied and edited, the DERIVED part is
    # recomputed every read and was never part of the body to begin with.
    return augment_shared_skill_body(name, body, app_slug, profile)


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

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

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import TYPE_CHECKING, Any, Literal

import msgspec

from .frontmatter import FrontmatterError, parse_frontmatter
from .skill_hub_git import GitError, TreeFile, same_content
from .skill_payload import (
    COPYING_FILE,
    ORIGIN_FILE,
    SkillOrigin,
    SkillSource,
    origin_for,
    skill_payload,
)

if TYPE_CHECKING:
    from ..files import WorkspaceFiles
    from .skill_hub import SkillHubStore, UpstreamState

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
    #: #589 — this workspace folder is a COPY (it carries an `.origin`
    #: manifest), not a skill written here. It still lives in the workspace and
    #: is still editable; it simply must not be mistaken for the user's own
    #: work when deciding source and default-on.
    is_copy: bool = False
    #: What the manifest says the copy came from — `shared` / `profile` for a
    #: materialized package skill, `hub` for one installed from the skill hub,
    #: `""` for a manifest that does not decode (an older or hand-written
    #: `.origin`: still a copy, of unknown source). A hub copy that happens to
    #: share a package skill's name is NOT that package's copy — its files came
    #: from the hub, so `effective_item_skills` keeps it a workspace skill
    #: (review round 2 of plan-skill-hub).
    copy_of: SkillSource | Literal[""] = ""
    #: The skill hub entry this folder is an installed copy of (its `.origin`),
    #: or ``""`` — not a copy, a package copy, or a fork's starting point (the
    #: user's own, plan-skill-hub-history W4). The one rule for "this item has
    #: that entry installed": the search tool and the chat card both read it.
    hub_entry: str = ""


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


async def read_workspace_skill(
    files: WorkspaceFiles, workspace_id: str, name: str
) -> tuple[str | None, SkillOrigin | None]:
    """:func:`load_workspace_skill` plus the folder's ``.origin`` — read in ONE
    batch, so knowing where a copy came from costs no extra round trip
    (`read_skill` counts a use of a skill hub copy from it). ``(None, None)``
    when there is no such skill; the origin is ``None`` when the folder has no
    `.origin` (written here and never published) or one that does not decode.
    A published folder tracks its own entry, so reading it counts as a use."""
    from ..filestore.batch import read_all_existing

    folder = f"/{WORKSPACE_SKILL_DIR}/{name}/"
    md, manifest = folder + "SKILL.md", folder + ORIGIN_FILE
    got = await read_all_existing(files, workspace_id, [md, manifest])
    if (raw := got.get(md)) is None:
        return None, None
    _front, body = _parse_frontmatter(raw)
    origin: SkillOrigin | None = None
    if (data := got.get(manifest)) is not None:
        try:
            origin = msgspec.json.decode(data, type=SkillOrigin)
        except msgspec.DecodeError:
            origin = None
    return _enforce_cap(name, body), origin


async def hub_entries_here(files: WorkspaceFiles, workspace_id: str) -> set[str]:
    """The skill hub entries this workspace holds an installed copy of —
    :attr:`SkillMeta.hub_entry`, the rule the Skills list reports too."""
    return {m.hub_entry for m in await workspace_skill_metas(files, workspace_id) if m.hub_entry}


async def skill_file_paths(
    files: WorkspaceFiles, workspace_id: str, name: str, *, wake: bool = True
) -> list[str]:
    """The paths of the skill's own files under ``.skill/<name>/`` — its
    bookkeeping (``.origin``, the copy marker) left out — sorted."""
    prefix = f"/{WORKSPACE_SKILL_DIR}/{name}/"
    bookkeeping = {prefix + ORIGIN_FILE, prefix + COPYING_FILE}
    return sorted(
        p for p in await files.ls(workspace_id, prefix, wake=wake) if p not in bookkeeping
    )


async def workspace_skill_payload(
    files: WorkspaceFiles, workspace_id: str, name: str
) -> dict[str, bytes]:
    """Every file under the workspace's ``.skill/<name>/``, keyed the way
    :func:`skill_payload` keys a package skill — the shape the skill hub stores
    and ``materialize_skill`` writes. ``.origin`` is left out: it says where THIS
    copy came from, which is the copy's business, not the skill's. Empty when
    there is no such folder."""
    prefix = f"/{WORKSPACE_SKILL_DIR}/{name}/"
    paths = await skill_file_paths(files, workspace_id, name)
    from ..filestore.batch import read_all

    return {
        path[len(prefix) :]: raw
        for path, raw in zip(paths, await read_all(files, workspace_id, paths), strict=True)
    }


async def workspace_skill_origin(
    files: WorkspaceFiles, workspace_id: str, name: str, *, wake: bool = True
) -> SkillOrigin | None:
    """The copy's ``.origin`` manifest, or ``None`` when the folder is not a
    copy (or does not exist). ``wake=False`` for a read across many
    workspaces: a reaped sandbox is answered from its durable copy rather than
    rebuilt (`WorkspaceFiles._warm`). A manifest that does not parse raises
    ``msgspec.DecodeError`` / ``ValidationError``."""
    from ..filestore.protocol import FileNotFound

    try:
        raw = await files.read(
            workspace_id, f"/{WORKSPACE_SKILL_DIR}/{name}/{ORIGIN_FILE}", wake=wake
        )
    except FileNotFound:
        return None
    return msgspec.json.decode(raw, type=SkillOrigin)


async def workspace_skill_metas(files: WorkspaceFiles, workspace_id: str) -> list[SkillMeta]:
    """``(name, description)`` for every well-formed skill under the workspace's
    ``.skill/`` dir, sorted by name. Unparseable / name-mismatched / nameless
    skills are skipped (logged) — same tolerance as the package loader, so one
    bad hand-edit can't break the whole index. Empty when there's no ``.skill/``."""
    prefix = f"/{WORKSPACE_SKILL_DIR}/"
    paths = await files.ls(workspace_id, prefix)
    from ..filestore.batch import read_all_existing
    from ..filestore.protocol import FileNotFound

    wanted = [
        path
        for path in sorted(paths)
        if path[len(prefix) :].count("/") == 1 and path.endswith("/SKILL.md")
    ]
    manifests = sorted(p for p in paths if p.endswith(f"/{ORIGIN_FILE}"))
    # The index is rendered every turn, so reading each SKILL.md with its own
    # call put a sandbox round trip per skill in front of every message — and
    # the manifests ride in the SAME batch (a second batch resolved the
    # workspace once more, on every message of every item holding a copy).
    # One batch, two tolerances: a SKILL.md is read STRICTLY, matching the
    # per-file loop this replaced (a bare `read`, so a skill that vanished
    # mid-listing raised out of here — a performance fix is not the place to
    # start tolerating a race nobody agreed to tolerate); a manifest that is
    # gone by read time simply makes its folder not a copy, which is what the
    # listing said before it ever read them.
    got = await read_all_existing(files, workspace_id, [*wanted, *manifests])
    copies: dict[str, SkillSource | Literal[""]] = {}
    installed: dict[str, str] = {}
    for path in manifests:
        if (raw := got.get(path)) is None:
            continue
        dir_name = path[len(prefix) : -len(f"/{ORIGIN_FILE}")]
        try:
            origin = msgspec.json.decode(raw, type=SkillOrigin)
        except msgspec.DecodeError:  # not JSON, or not this shape — still a copy
            copies[dir_name] = ""
            continue
        copies[dir_name] = origin.source
        if origin.source == "hub" and origin.entry and not origin.forked:
            installed[dir_name] = origin.entry
    out: list[SkillMeta] = []
    for path in wanted:
        if (raw := got.get(path)) is None:
            raise FileNotFound(path)
        dir_name = path[len(prefix) : -len("/SKILL.md")]
        meta = _workspace_skill_meta(raw, dir_name)
        if meta is not None:
            out.append(
                msgspec.structs.replace(
                    meta,
                    is_copy=dir_name in copies,
                    copy_of=copies.get(dir_name, ""),
                    hub_entry=installed.get(dir_name, ""),
                )
            )
    return out


def _workspace_skill_meta(raw: bytes, dir_name: str) -> SkillMeta | None:
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


async def advertised_workspace_skills(
    files: WorkspaceFiles, workspace_id: str, prefs: Mapping[str, bool] | None = None
) -> list[SkillMeta]:
    """The workspace skills this turn tells the agent about: `.skill/` read live,
    minus any the item toggled OFF (`prefs[name]` is False, #380 — workspace
    skills are default-on, so only an explicit False hides one).

    Deliberately NOT `effective_item_skills`: that resolver makes a copy of a
    package skill answer as the skill it copied (#589), so a copy of a
    default-off one is "not effective" while this block still lists it and
    `read_skill` still loads it (it too refuses only an explicit off). Whoever
    needs to know what the agent was told — the block below, and the
    `read_skill` grant — must ask THIS rule, or the turn advertises a skill it
    has no tool to load, or withdraws a tool for a skill it just advertised."""
    metas = await workspace_skill_metas(files, workspace_id)
    return [m for m in metas if prefs.get(m.name) is not False] if prefs else metas


async def build_workspace_skills_block(
    files: WorkspaceFiles, workspace_id: str, prefs: Mapping[str, bool] | None = None
) -> str:
    """Read the workspace's `.skill/` live and render the index block (or ``""``)."""
    return workspace_skills_block(await advertised_workspace_skills(files, workspace_id, prefs))


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
    #: What the copy is OF — ``shared`` / ``profile`` / ``hub`` (``""`` when not
    #: a copy). ``source`` cannot say it: a copy of a package skill this App
    #: does not declare has no row to shadow and lists as ``workspace`` just
    #: like a hub copy, and the panel words Reset / Update by the origin
    #: (#826 review round 1).
    copy_of: SkillSource | Literal[""] = ""
    #: :attr:`SkillMeta.hub_entry` of the workspace folder, carried through.
    hub_entry: str = ""


def effective_item_skills(
    app_slug: str,
    profile: str,
    prefs: Mapping[str, bool],
    workspace_metas: list[SkillMeta],
    *,
    tools: Collection[str] | None,
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
    from .shared_skills import plugin_skills_for, shared_skill_metas

    declared = list(load_app_manifest(app_slug).agent.skills)
    prof_skills = load_profile(app_slug, profile).skills
    default_shared = set(declared if prof_skills is UNSET else prof_skills)
    # name → (meta, source, default_on); later writes shadow earlier ones.
    rows: dict[str, tuple[SkillMeta, str, bool]] = {}
    for m in shared_skill_metas(declared):
        rows[m.name] = (m, "shared", m.name in default_shared)
    # #847/#848: a view plugin's skill, for an item that can draw a view — by the
    # RESOLVED `tools` (required, so no caller can forget it and quietly show a
    # picker that disagrees with the prompt). On by default: the operator
    # installed the plugin for the items that can use it.
    for m in plugin_skills_for(tools):
        rows[m.name] = (m, "shared", True)
    for m in list_skills(app_slug, profile):
        rows[m.name] = (m, "profile", True)
    for m in workspace_metas:
        prior = rows.get(m.name)
        if m.is_copy and m.copy_of != "hub" and prior is not None:
            # A copy of a baked-in skill answers as the skill it copied. Its
            # DESCRIPTION comes from the copy — that is the text actually read
            # this turn, and the AI may have edited it — but its source and
            # default-on stay the package's, so using a default-off skill once
            # cannot quietly turn it on for good. A copy installed from the
            # skill hub is not that, whatever its name: its files never came
            # from the package, so it is a workspace skill like any other
            # (and the panel offers Publish on it, as on any workspace skill).
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
                copy_of=meta.copy_of,
                hub_entry=meta.hub_entry,
            )
        )
    return out


def describe_wui_tools(names: Sequence[str]) -> str:
    """The tools a WUI in this app may call, named.

    This CANNOT live in the skill: which tools an app grants is per-app, and the
    model cannot tell a package tool from a built-in by looking — `data-fetch`
    and `read_file` are both just names in its toolset. Left to guess, it either
    declares something a WUI can never call or never declares anything."""
    if not names:
        return (
            "## Tools this app offers its WUIs\n\n"
            "None. This app grants no package tools, so a page here cannot reach "
            "anything outside the item — leave `tools:` out of the view file."
        )
    return "\n".join(
        [
            "## Tools this app offers its WUIs",
            "",
            "These, and only these, may go in a view file's `tools:` and be called",
            "with `workspace.callTool(...)`. Your other tools are built-ins; a page",
            "cannot call them, and declaring one fails at the call:",
            "",
            *(f"- `{n}`" for n in names),
        ]
    )


def augment_shared_skill_body(
    name: str,
    body: str,
    app_slug: str | None,
    profile: str | None,
    *,
    wui_tools: Sequence[str] | None = None,
) -> str:
    """Append machine-derived, always-current detail to a shared skill whose static body is
    deliberately purpose-only (plan §3). ``author-workflow`` gets the DSL grammar (derived
    from the schema — P5) + this app's capability/tool boundaries (P6), so the AI drafts
    against the real grammar and knows what it can/can't do, without the skill drifting.
    ``wui`` gets the tools this app offers its pages, for the same reason.

    ``wui_tools`` is ``None`` when the caller had no resolved toolset to ask —
    then the section is OMITTED rather than rendered empty, because "this app
    grants none" and "nobody asked" are different claims and only one of them is
    ever safe to make up."""
    if name == "wui":
        return body if wui_tools is None else "\n\n".join([body, describe_wui_tools(wui_tools)])
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
    from .shared_skills import shared_skill_source

    src = shared_skill_source(name)
    return ("shared", src) if src is not None else None


def readonly_skill_path(path: str) -> bool:
    """Whether ``path`` is inside the copy of a readonly shared skill -- the
    facade's `readonly` check (docs/plan-ai-reads-docs.md P1). Decided by the
    SHIPPED ``SKILL.md`` of the skill the folder is named for, never by the copy:
    a readonly shared skill's name is reserved in every workspace.

    Judged on the path as the filesystem will resolve it: `/./`, `//` and `x/..`
    are normalised first, or those spellings wrote straight into the copy
    (review #865 round 1)."""
    import posixpath

    parts = posixpath.normpath("/" + path.lstrip("/")).lstrip("/").split("/")
    if len(parts) < 2 or parts[0] != WORKSPACE_SKILL_DIR:
        return False
    from .shared_skills import shared_skill_readonly

    return shared_skill_readonly(parts[1])


def upstream_readonly(app_slug: str | None, profile: str | None, name: str) -> bool:
    """Whether the copy of this name follows upstream as a readonly skill: a
    SHARED skill whose shipped ``SKILL.md`` says so. A profile skill of the same
    name shadows it and is not refreshed as one -- though the name stays reserved,
    so the facade still refuses writes to it (`readonly_skill_path`;
    docs/plan-ai-reads-docs.md P1, as built)."""
    from .shared_skills import shared_skill_readonly

    found = _skill_source(app_slug, profile, name)
    return found is not None and found[0] == "shared" and shared_skill_readonly(name)


async def materialize_skill(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
) -> None:
    """#589: copy a baked-in skill's files into the workspace so the body's own
    instructions resolve — ``read .skill/<name>/references/glossary.md`` and
    ``exec(["python", ".skill/<name>/scripts/x.py"])`` only work if the files are
    actually there. (The body has to name that full path: `read_file` resolves
    from the workspace root and `read_skill` returns the body alone.)

    Copy-if-absent: a workspace copy already present is left completely alone.
    That is the whole point — the AI is meant to tweak these scripts, and an
    overwrite would delete its work. Refreshing a copy is a separate, explicit
    action, never a side effect of using the skill.

    Writes go through ``WorkspaceFiles``, which routes to the item's live sandbox
    when one is already awake (so the files are usable THIS turn) and to the
    durable store when it is cold — without waking it, which `read_skill`
    promises. A workspace over quota fails here, loudly, like any other write.
    """
    from ..files import system_writes

    # The platform writing the copy -- the one writer a readonly skill's copy
    # admits (docs/plan-ai-reads-docs.md P1).
    with system_writes():
        await _materialize(files, workspace_id, app_slug, profile, name)


async def _materialize(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
) -> None:
    """`materialize_skill`'s work, run under `system_writes`."""
    prefix = f"/{WORKSPACE_SKILL_DIR}/{name}/"
    marker = prefix + COPYING_FILE
    here = await files.ls(workspace_id, prefix)
    readonly = upstream_readonly(app_slug, profile, name)
    if here:
        # A readonly skill (docs/plan-ai-reads-docs.md P1) is the exception: it is
        # reference the AI reads, never edits, so its copy follows what the image
        # ships -- replaced whole whenever the copy's `.origin` no longer matches.
        if not readonly:
            return
        origin = await workspace_skill_origin(files, workspace_id, name)
        if origin is not None and origin.source != "shared":
            # A hub copy that happens to carry the name is not this skill's copy;
            # asking for its upstream without a hub raised (review #865 round 1).
            return
        if origin is not None:
            if marker in here:
                # finished, and cut short before the marker went
                await _delete_if_there(files, workspace_id, marker)
            # `.origin` says what was shipped, not what is here: two first reads
            # at once can leave it over files one of them cleared (review #865
            # round 4). `here` is already listed, so this costs no round trip.
            missing = any(prefix + rel not in here for rel in origin.files)
            up = await skill_upstream(files, workspace_id, app_slug, profile, name)
            if missing or (up is not None and up.update_available):
                await refresh_skill(files, workspace_id, app_slug, profile, name, force=True)
            return
        # No `.origin`. With the marker it is the platform's own copy, cut short
        # (review #865 round 1): nobody can edit or delete it and the refresh
        # needs the manifest, so it would stay half-written for good -- it is
        # cleared and copied again. Without it the folder is the person's own,
        # written before the name was reserved, and is never deleted (round 2).
        # Not judged by its bytes: a rollout both cuts a copy short and ships
        # different docs, so a copy's bytes never match the image that next
        # reads it (round 3).
        if marker not in here:
            return
        # The marker goes LAST: a clearing cut short in its turn must still read
        # as the platform's copy.
        for path in sorted(here, key=lambda p: p == marker):
            await _delete_if_there(files, workspace_id, path)
    found = _skill_source(app_slug, profile, name)
    if found is None:
        return
    source, src_dir = found
    payload = skill_payload(src_dir)
    # A skill that is nothing but its SKILL.md has nothing to materialize, and
    # copying it anyway would be pure cost: the copy shadows the package version,
    # so the body stops tracking upstream and the skill starts reporting as a
    # workspace one. Most shipped skills are exactly that shape, so the common
    # case must stay untouched — only a skill that actually brings files becomes
    # a local copy. `author-skill` is one (it ships `references/`), so from its
    # first read a workspace holds its own copy and a later edit to the shipped
    # guide reaches that workspace only through the skills panel's Refresh.
    if set(payload) <= {"SKILL.md"}:
        return
    manifest = msgspec.json.encode(origin_for(source, payload))
    # The whole copy is one operation (#538, as `install_hub_skill`): checked
    # once, up front, so a workspace with room for some of the files and not the
    # rest refuses cleanly instead of keeping half a copy with no `.origin`
    # (review #865 round 1).
    await files.ensure_room_for(workspace_id, sum(len(d) for d in payload.values()) + len(manifest))
    if readonly:
        await files.write(workspace_id, marker, b"")
    for rel, data in payload.items():
        await files.write(workspace_id, f"/{WORKSPACE_SKILL_DIR}/{name}/{rel}", data)
    # Written LAST: until it exists the copy is incomplete, and a manifest that
    # outlived a half-written copy would claim shipped bytes for files that were
    # never written.
    await files.write(
        workspace_id,
        f"/{WORKSPACE_SKILL_DIR}/{name}/{ORIGIN_FILE}",
        manifest,
    )
    if readonly:
        await _delete_if_there(files, workspace_id, marker)


async def _delete_if_there(files: WorkspaceFiles, workspace_id: str, path: str) -> None:
    """Delete ``path``; one already gone is what was wanted. Two reads of one
    copy clear it at once, and a re-run meets what the run before it already
    removed (review #865 round 3)."""
    import contextlib

    from ..filestore.protocol import FileNotFound

    with contextlib.suppress(FileNotFound):
        await files.delete(workspace_id, path)


class Upstream(msgspec.Struct, frozen=True):
    """What a copy's ``.origin`` points at, resolved NOW for one viewer.

    ``origin`` is the copy's own manifest (the one read to get here — read
    once, carried, so callers do not read it again). ``files`` is what upstream
    ships today as ``{rel: sha256}`` — for a hub entry straight off its row,
    which is why "has an update" costs no blob reads. ``payload`` is the bytes,
    loaded only when asked (``with_payload``): only Refresh needs them. Both are
    empty unless ``state`` is ``live``."""

    source: SkillSource
    state: UpstreamState
    origin: SkillOrigin
    files: dict[str, str]
    payload: dict[str, bytes]
    entry: str = ""
    #: For a skill hub entry in git: the commit of its current version. Its
    #: files are then read from git when needed, and `files` / `payload` stay
    #: empty (plan-skill-hub-history G12–G14).
    commit: str = ""


async def resolve_upstream(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
    *,
    hub: SkillHubStore | None = None,
    viewer: str = "",
    with_payload: bool = False,
) -> Upstream | None:
    """The copy's upstream, or ``None`` when ``.skill/<name>/`` is not a copy.

    Three sources, one answer shape. A package skill (``shared`` / ``profile``)
    is found by NAME and is ``live`` or, once retired from the package,
    ``deleted``. A skill hub copy is found by the ENTRY ID its manifest recorded
    and can also be ``unpublished`` — the owner took it private and this viewer
    is no longer on the list (plan Q5). The hub is required for a hub copy: a
    caller without one would otherwise read every hub copy as ``deleted``, so
    that is a wiring error, raised, not a state.
    """
    origin = await workspace_skill_origin(files, workspace_id, name)
    if origin is None:
        return None
    if origin.source == "hub":
        if hub is None:
            raise ValueError(
                f"{name!r} was installed from the skill hub, but no skill hub was given"
            )
        state, entry = hub.state_for(origin.entry, viewer)
        if entry is None:
            return Upstream(source="hub", state=state, origin=origin, files={}, payload={})
        if entry.commit:
            return Upstream(
                source="hub",
                state="live",
                origin=origin,
                files={},
                payload={},
                entry=origin.entry,
                commit=entry.commit,
            )
        # Not migrated into git yet: its per-file hashes are on the row.
        payload = await hub.payload_of(origin.entry) if with_payload else {}
        return Upstream(
            source="hub",
            state="live",
            origin=origin,
            files=dict(entry.origin.files),
            payload=payload,
            entry=origin.entry,
        )
    found = _skill_source(app_slug, profile, name)
    if found is None:
        return Upstream(source=origin.source, state="deleted", origin=origin, files={}, payload={})
    source, src_dir = found
    payload = skill_payload(src_dir)
    return Upstream(
        source=source,
        state="live",
        origin=origin,
        files=origin_for(source, payload).files,
        payload=payload if with_payload else {},
    )


class SkillUpstream(msgspec.Struct, frozen=True):
    """The Skills panel's two facts about a copy's upstream."""

    state: UpstreamState
    update_available: bool


async def skill_upstream(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
    *,
    hub: SkillHubStore | None = None,
    viewer: str = "",
) -> SkillUpstream | None:
    """The copy's upstream state and whether it now ships something this copy
    does not have; ``None`` for a folder that is not a copy.

    "Has an update" compares what was SHIPPED (recorded in ``.origin``) against
    what upstream ships now — deliberately not against the files on disk here.
    An edit made in this workspace is not an upstream change, and offering
    "update" for it would invite the user to press a button whose only honest
    outcome is "skipped". Only a ``live`` upstream can have one.
    """
    up = await resolve_upstream(
        files, workspace_id, app_slug, profile, name, hub=hub, viewer=viewer
    )
    if up is None:
        return None
    if up.state != "live" or up.origin.forked:
        # A fork's starting point is the user's own; the entry moving on is
        # not an update to it (G23).
        return SkillUpstream(state=up.state, update_available=False)
    if up.commit and up.origin.commit:
        # G13: one comparison of two strings — no file is read, from git or here.
        return SkillUpstream(state="live", update_available=up.origin.commit != up.commit)
    if up.commit:
        # A copy made before the git store (G17): its sha256 map against the
        # current version's, computed from git once per version.
        assert hub is not None  # `resolve_upstream` raises for a hub copy without one
        current = await hub.file_sha256s(up.entry)
        return SkillUpstream(state="live", update_available=up.origin.files != current)
    return SkillUpstream(state="live", update_available=up.origin.files != up.files)


class FolderInTheWay(msgspec.Struct, frozen=True):
    """The fact behind an install refusal: ``.skill/<name>/`` is occupied, and
    whose hub copy it is when it is one (``""`` for a hand-written folder, a
    package copy, or a copy of an entry the viewer may not read). Two doors
    render it (plan-skill-hub-ui-polish D16): the tool tells the model the
    English ``sentence``; the route sends the person a code with ``owner`` and
    ``path`` for the front end to word in their language."""

    name: str
    owner: str = ""

    @property
    def path(self) -> str:
        return f"{WORKSPACE_SKILL_DIR}/{self.name}/"

    def sentence(self) -> str:
        whose = f"{self.owner}'s " if self.owner else ""
        return (
            f"this workspace already has {whose}'{self.path}' — remove or rename that folder "
            "first, then install again"
        )

    def code(self) -> dict[str, str]:
        return {"error": "folder_in_the_way", "owner": self.owner, "path": self.path}


async def skill_folder_in_the_way(
    files: WorkspaceFiles,
    workspace_id: str,
    hub: SkillHubStore,
    name: str,
    viewer: str,
    *,
    wake: bool = True,
) -> FolderInTheWay | None:
    """The refusal an install gets when ``.skill/<name>/`` already exists —
    one fact, shared by the tool and the route so the two doors refuse alike
    — or ``None`` when the name is free. Never overwrite: the folder may be
    the user's own skill, or an earlier install they have since edited. It
    says WHOSE copy it is when it is one, so "already have it" and "name
    clash" read differently (plan install step 4). Listed, not read: a
    folder with any file of its own is in the way. ``wake`` as for
    :func:`workspace_skill_origin` — the install dialog asks this of every
    workspace it offers. A manifest that does not parse names no owner: the
    folder is still in the way."""
    if not await skill_file_paths(files, workspace_id, name, wake=wake):
        return None
    try:
        origin = await workspace_skill_origin(files, workspace_id, name, wake=wake)
    except (msgspec.DecodeError, msgspec.ValidationError):
        origin = None
    owner = ""
    # A fork's starting point (`forked`) is the viewer's own skill: naming the
    # original's owner on it read as someone else's copy (round 2).
    if origin is not None and origin.source == "hub" and origin.entry and not origin.forked:
        _state, theirs = hub.state_for(origin.entry, viewer)
        if theirs is not None:
            owner = theirs.owner
    return FolderInTheWay(name=name, owner=owner)


async def install_hub_skill(
    files: WorkspaceFiles, workspace_id: str, hub: SkillHubStore, entry_id: str
) -> str:
    """Copy a skill hub entry into the workspace as ``.skill/<name>/`` and return
    the name. The hub-sourced twin of :func:`materialize_skill`: files first,
    ``.origin`` LAST (until it exists the copy is incomplete, and a manifest
    that outlived a half-written copy would claim shipped bytes for files that
    were never written). The caller has already decided the entry may be
    installed and that nothing sits at that name — this only writes.
    """
    entry = hub.get(entry_id)
    assert entry is not None  # the caller checked `state_for` first
    # The files and the manifest come from ONE read of the row: reading it
    # again for the files let a publish in between give a copy that names one
    # version and holds another (review round 1, defect #10).
    payload = (
        await hub.repos.read(entry_id, entry.commit)
        if entry.commit
        else await hub.payload_of(entry_id)
    )
    await _write_copy(files, workspace_id, entry.name, payload, hub.copy_manifest(entry_id, entry))
    return entry.name


async def fork_hub_version(
    files: WorkspaceFiles, workspace_id: str, hub: SkillHubStore, entry_id: str, revision: str
) -> str:
    """Copy the version `revision` names into the workspace as a fork's
    starting point (§8, G23) and return the name: the files of that version,
    and an `.origin` marked `forked` so it is never offered the entry's newer
    versions. Raises :class:`UnknownRevision` for a revision that is not one
    of the entry's versions. The caller has checked the entry is readable and
    the name is free."""
    entry = hub.get(entry_id)
    assert entry is not None
    old = await hub.version(entry_id, revision)
    payload = await hub.repos.read(entry_id, old.commit)
    manifest = SkillOrigin(source="hub", files={}, entry=entry_id, commit=old.commit, forked=True)
    await _write_copy(files, workspace_id, entry.name, payload, manifest)
    return entry.name


async def _write_copy(
    files: WorkspaceFiles,
    workspace_id: str,
    name: str,
    payload: Mapping[str, bytes],
    origin: SkillOrigin,
) -> None:
    root = f"/{WORKSPACE_SKILL_DIR}/{name}"
    manifest = msgspec.json.encode(origin)
    # The whole folder is one operation (#538): checked once up front, so a
    # workspace with room for the first file and not the rest refuses cleanly
    # instead of leaving half a folder with no `.origin` — which would then
    # read as a hand-written skill of that name and block the next install.
    # The manifest's bytes are part of that check: a check that counted the
    # files and not the `.origin` written after them left exactly that half
    # folder (review round 2).
    await files.ensure_room_for(
        workspace_id, sum(len(data) for data in payload.values()) + len(manifest)
    )
    for rel, data in payload.items():
        await files.write(workspace_id, f"{root}/{rel}", data)
    await files.write(workspace_id, f"{root}/{ORIGIN_FILE}", manifest)


class SkillRefresh(msgspec.Struct, frozen=True):
    """What a refresh actually did, per file. ``skipped`` is the interesting one:
    those files were edited here, so they were left exactly as they are."""

    updated: list[str]
    skipped: list[str]
    removed: list[str]


async def refresh_skill(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
    *,
    force: bool = False,
    hub: SkillHubStore | None = None,
    viewer: str = "",
) -> SkillRefresh:
    """Bring a copied skill up to the version the package now ships.

    ``force`` is "reset to factory": every shipped file is restored, including
    ones edited here. It is destructive on purpose and only ever because the user
    said so — never as a side effect of using or updating the skill.

    Per file, never wholesale. A file still byte-identical to what was shipped is
    replaced; a file that was edited here is left alone and reported. Overwriting
    everything would delete the AI's tweaks — the very thing this feature exists
    to allow — and doing it on an action labelled "update" would destroy them at
    the moment the user least expects it.
    """
    from ..files import system_writes

    # The platform bringing a copy up to date -- a writer a readonly skill's copy
    # admits (docs/plan-ai-reads-docs.md P1).
    with system_writes():
        return await _refresh(
            files, workspace_id, app_slug, profile, name, force=force, hub=hub, viewer=viewer
        )


async def _refresh(
    files: WorkspaceFiles,
    workspace_id: str,
    app_slug: str | None,
    profile: str | None,
    name: str,
    *,
    force: bool,
    hub: SkillHubStore | None,
    viewer: str,
) -> SkillRefresh:
    """`refresh_skill`'s work, run under `system_writes`."""
    root = f"/{WORKSPACE_SKILL_DIR}/{name}"
    up = await resolve_upstream(
        files, workspace_id, app_slug, profile, name, hub=hub, viewer=viewer, with_payload=True
    )
    # Not a copy, or an upstream that is gone / closed to this viewer: nothing
    # to bring, and nothing here is touched — the copy is the workspace's own.
    if up is None or up.state != "live" or up.origin.forked:
        return SkillRefresh(updated=[], skipped=[], removed=[])
    origin, source = up.origin, up.source
    if up.commit:
        # A skill hub entry in git (G14): the baseline is the tree of the commit
        # this copy came from (or, for a copy made before the git store, its
        # sha256 map), the upstream is the current commit's tree, and content
        # is read from git only for the files being written.
        assert hub is not None
        repos, entry_id, commit = hub.repos, up.entry, up.commit
        base: Mapping[str, TreeFile | str] = dict(origin.files)
        if origin.commit:
            try:
                base = await repos.tree(entry_id, origin.commit)
            except GitError:
                # `.origin` is the copy's own file and can name a version the
                # repo does not have. Without what was shipped there is no
                # telling an edit from upstream's change; only a reset (which
                # needs no baseline) is safe to do.
                if not force:
                    raise SkillError(
                        f"this copy of {name!r} no longer records which version it came from "
                        "— reset it to the skill hub's version instead (edited files are replaced)"
                    ) from None
                base = {}
        current: Mapping[str, TreeFile | str] = await repos.tree(entry_id, commit)

        async def fetch(paths: list[str]) -> dict[str, bytes]:
            return await repos.read(entry_id, commit, paths=paths) if paths else {}

        # The version just compared against — not the row read again, which a
        # publish in between would move.
        manifest = SkillOrigin(source="hub", files={}, entry=entry_id, commit=commit)
    else:
        payload = up.payload
        base = dict(origin.files)
        current = origin_for(source, payload).files

        async def fetch(paths: list[str]) -> dict[str, bytes]:
            return {p: payload[p] for p in paths}

        manifest = origin_for(source, payload, entry=up.entry)
    updated, skipped, removed = await _three_way(
        files, workspace_id, root, base=base, current=current, fetch=fetch, force=force
    )
    await files.write(workspace_id, f"{root}/{ORIGIN_FILE}", msgspec.json.encode(manifest))
    return SkillRefresh(updated=updated, skipped=skipped, removed=removed)


def _shipped_matches(shipped: TreeFile | str, data: bytes) -> bool:
    """Whether `data` is what was shipped as `shipped` — a git tree file
    (blob id, or sha256 for LFS) or a manifest's sha256."""
    if isinstance(shipped, str):
        return hashlib.sha256(data).hexdigest() == shipped
    return same_content(shipped, data)


async def _three_way(
    files: WorkspaceFiles,
    workspace_id: str,
    root: str,
    *,
    base: Mapping[str, TreeFile | str],
    current: Mapping[str, TreeFile | str],
    fetch: Callable[[list[str]], Awaitable[dict[str, bytes]]],
    force: bool,
) -> tuple[list[str], list[str], list[str]]:
    """The refresh rule, per file, never wholesale, with `base` = what this copy
    was shipped and `current` = what upstream ships now (each a git tree or a
    sha256 map): upstream did not change it → nothing to bring; it changed and
    the copy still holds what was shipped → replaced; the copy was edited →
    left alone and reported; upstream dropped it → removed only when the copy
    still holds what was shipped. `force` restores every shipped file."""
    from ..filestore.protocol import FileNotFound

    # A sha256 baseline against a git file that is not LFS needs that file's
    # bytes to compare; read once, together, and reused if it is written.
    mixed = [
        p
        for p, cur in current.items()
        if isinstance(base.get(p), str) and isinstance(cur, TreeFile) and cur.lfs is None
    ]
    fetched = await fetch(mixed) if mixed and not force else {}

    def changed_upstream(rel: str) -> bool:
        shipped, now = base.get(rel), current[rel]
        if shipped is None:
            return True
        if isinstance(shipped, str) and isinstance(now, str):
            return shipped != now
        if isinstance(shipped, TreeFile) and isinstance(now, TreeFile):
            return shipped.blob_id != now.blob_id
        if isinstance(shipped, str) and isinstance(now, TreeFile):
            sha = now.lfs[0] if now.lfs is not None else hashlib.sha256(fetched[rel]).hexdigest()
            return sha != shipped
        raise AssertionError(f"a git baseline against a non-git upstream: {rel}")

    async def unchanged_here(rel: str) -> bool:
        """Whether the copy still holds the bytes shipped as `base[rel]`."""
        try:
            here = await files.read(workspace_id, f"{root}/{rel}")
        except FileNotFound:
            return False
        return _shipped_matches(base[rel], here)

    to_write: list[str] = []
    skipped: list[str] = []
    for rel in sorted(current):
        if not force and not changed_upstream(rel):
            # Upstream did not touch this file, so there is nothing to bring —
            # regardless of what happened to it here. "Nothing to bring" is not
            # the same as "skipped": reporting it would bury the files the user
            # actually needs to know about among every unchanged one.
            continue
        if not force and rel in base and not await unchanged_here(rel):
            skipped.append(rel)
            continue
        to_write.append(rel)
    data = {**fetched, **(await fetch([p for p in to_write if p not in fetched]))}
    for rel in to_write:
        await files.write(workspace_id, f"{root}/{rel}", data[rel])
    removed: list[str] = []
    for rel in sorted(base):
        if rel in current:
            continue
        # Retired upstream. Dropping follows the same rule as changing: a file the
        # AI edited is its work now, and upstream removing the original is not a
        # licence to delete it.
        if not force and not await unchanged_here(rel):
            skipped.append(rel)
            continue
        # A refresh cut short after this delete keeps the old `.origin`, which
        # still lists the file, so the next one meets it gone (review #865 round 3).
        await _delete_if_there(files, workspace_id, f"{root}/{rel}")
        removed.append(rel)
    return sorted(to_write), sorted(skipped), sorted(removed)


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
    from .shared_skills import load_shared_skill, shared_skill_source

    await materialize_skill(files, workspace_id, app_slug, profile, name)
    body = await load_workspace_skill(files, workspace_id, name)
    if body is None and shared_skill_source(name) is not None:
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
    from ..files import WorkspaceFull
    from ..quota.disk_ledger import UserDiskFull

    sections: list[str] = []
    for name in names:
        try:
            body = await resolve_skill_body(files, workspace_id, app_slug, profile, name)
        except SkillError as e:
            sections.append(f"### {name}\n\n(could not load: {e})")
            continue
        except (WorkspaceFull, UserDiskFull) as e:
            # A copy that does not fit is refused whole (review #865 round 1), on
            # every try until space is freed -- a note, like any skill that
            # cannot load, rather than a turn that cannot start (round 2). The
            # owner's total across items refuses too, and is not a WorkspaceFull
            # (round 3).
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
    """`frontmatter.parse_frontmatter` in skill flavour — the shared parser, with
    its error retyped so every `except SkillError` in this module keeps catching
    a malformed `---` block."""
    try:
        return parse_frontmatter(raw)
    except FrontmatterError as e:
        raise SkillError(str(e)) from e

"""The skill hub — skills flow between people without the operator's git.

A skill a user wrote lives in ONE workspace (``.skill/<name>/``). Until now the
only way to hand it to somebody else was for the operator to copy it into
``sample-skills/`` and redeploy — every share an ops ticket. This module is the
fourth source ``read_skill`` can materialize from, and the one users fill
themselves. Design and the ten decisions behind it: ``docs/plan-skill-hub.md``.

Storage is two halves, deliberately (``docs/plan-skill-hub-history.md``):

* one specstar row per published skill (:class:`SkillHubEntry`) — the metadata
  everything else keys on: owner, visibility, the review, and ``commit``, the
  version the row's revision is current in;
* the files as one bare git repo per entry (``skill_hub_git``), whose
  ``master`` is the current version and whose tags name each revision.
  Entries published before the git store have no ``commit`` and are read from
  the FileStore namespace their files were written to (``blobs``) until they
  are migrated.

Identity is ``(owner, name)`` over the row's stable resource id. Installed
copies and forks point at the ID, so transferring ownership — an explicit,
mutable ``owner`` field rather than ``created_by`` — breaks nothing downstream.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import datetime as dt
import hashlib
import logging
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

import msgspec
from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import (
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionNotFoundError,
)

from ..perm import Actor, Permission, authorize
from ..resources.groups import groups_of
from .skill_hub_git import GitError
from .skill_payload import SkillOrigin
from .skills import SKILL_BODY_CAP, SkillError, _parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from ..filestore.protocol import FileStore
    from .skill_hub_git import SkillHubRepos, TreeFile

#: What an entry is to one viewer. `live`: readable. `unpublished`: exists but
#: this viewer may not read it (the owner made it private / restricted them out
#: — Q5). `deleted`: soft-deleted or never existed — from outside there is no
#: difference (Q10).
logger = logging.getLogger(__name__)

UpstreamState = Literal["live", "unpublished", "deleted"]

#: Where an entry published BEFORE the git store kept its files: a synthetic
#: workspace id per version (`skill-hub:<entry id>:<version>`). Read until the
#: entry is moved into git, and never deleted (plan-skill-hub-history W18) —
#: they are the only original of that version.
_BLOB_PREFIX = "skill-hub:"

#: The most one entry may hold, all files together. Publishing reads the folder
#: whole into memory and stores it in the durable store outside any user quota,
#: and every installer's workspace then pays for it (review round 1).
SKILL_HUB_MAX_BYTES = 20 * 1024 * 1024
#: The most files one entry may hold (plan-skill-hub-history G11) — checked with
#: the size, in the same function, by every caller that checks one.
SKILL_HUB_MAX_FILES = 1000
#: How many times a first publish that meets another first publish of the same
#: name steps aside and retries before telling the publisher to try later (G26).
_FIRST_PUBLISH_TRIES = 5
#: A pending draft older than this is a dead publisher's, not one in flight
#: (§7 Q3): far longer than a publish takes.
_DRAFT_TTL = dt.timedelta(minutes=10)
#: What a publish says when its row went away mid-publish (round 3).
_VANISHED = (
    "the skill hub entry for {name!r} was removed while it was being published — publish it again"
)
#: How many times a row write re-reads after losing its compare-and-swap.
_WRITE_TRIES = 5
# Versions whose sha256 map `file_sha256s` keeps; a version never changes, so
# an entry only ever needs its current one — the bound is entries, not versions.
_SHA256_CACHE = 256


class SkillHubReview(Struct):
    """What the AI reviewer said at publish time.

    Two verdicts only. There is no "unreviewed": a publish that could not be
    reviewed does not publish (plan Q9) — no AI means the system is broken, and
    a broken system is not the moment to open a gap.
    """

    verdict: Literal["ok", "notes"]
    notes: list[str] = field(default_factory=list)
    #: Which model reviewed it, so a note can be read against the model that
    #: wrote it — guidance is model-specific, and so is a review of guidance.
    model: str = ""


class SkillHubEntry(Struct):  # → resource "skill-hub-entry"
    """One published skill. Its versions live in git (`commit` = the current
    one); an entry published before the git store also names its old files
    (`blobs`). See the module docstring."""

    #: Explicit and mutable — NOT `created_by`. Ownership transfers (people
    #: leave, teams reorganise), and the transfer must not change the entry's
    #: id, which is what every installed copy's `.origin` points at.
    owner: str
    #: = the workspace folder name = the frontmatter `name`. Identity with `owner`.
    name: str
    #: From the frontmatter. The agent's index and the hub's search both key on
    #: it, so it is also what the AI review scrutinises hardest.
    description: str
    #: Where it was published from. "Edit" opens this item (plan D6); the four
    #: ways that can fail are handled by the edit route, not here.
    source_item: str
    source_app: str
    source_profile: str
    #: The AI reviewer's verdict at publish time. Required: no review, no row.
    review: SkillHubReview
    #: The original's entry id when this is a fork; "" for a root. A string
    #: rather than `str | None` for the same reason `Notification.outbound` is:
    #: it is INDEXED, the listing filters on it ("roots only"), and the empty
    #: string is the value that filter can ask for.
    forked_from: str = ""
    #: Tool names the body mentions, found by scanning for registered names
    #: (plan Q2). Installing into an app that lacks one is allowed but announced.
    referenced_tools: list[str] = field(default_factory=list)
    #: The platform's own permission model, reused whole. `visibility` defaults
    #: to `public` (plan Q6); `private` is what "unpublish" means (plan Q5).
    permission: Permission = field(default_factory=Permission)
    #: For an entry published before the git store: the FileStore namespace
    #: holding its files (`""` = the pre-versioned `skill-hub:<id>`). Read
    #: until the entry is in git; a version in git never sets it.
    blobs: str = ""
    #: The git commit this revision's version is (plan-skill-hub-history G7):
    #: master when the revision was written, and the commit its `r-` tag
    #: names. "" for an entry published before the git store (read from
    #: `blobs` until migrated) and for a pending draft.
    commit: str = ""
    #: A first publish's draft, made only so the name can be checked (G26) —
    #: invisible to every reader until the version is in git. Distinct from a
    #: pre-git entry, which also has no `commit` but is live.
    pending: bool = False
    #: An entry published before the git store: `origin_for("hub", payload)`,
    #: the per-file sha256 of what it ships — how its copies are compared until
    #: it is migrated. Not written for a version in git: that version's files
    #: are its commit's tree (plan-skill-hub-history G12).
    origin: SkillOrigin = field(default_factory=lambda: SkillOrigin(source="hub", files={}))
    #: When what the entry ships last changed — written by publish and
    #: rollback only, the list's 「最近更新」 (plan-skill-hub-ux-redo D4). Not
    #: the revision's timestamp: a permission change or a transfer is a new
    #: revision too. `None` for a row written before the field (no backfill —
    #: its next publish dates it).
    content_at: dt.datetime | None = None


# ── what publishing checks ───────────────────────────────────────────────────

#: A `references/...` path the body names. Stops at whitespace and at the
#: punctuation a path can never contain in a link or a code span (the closing
#: backtick or bracket, a comma, a quote); what it may still carry — a full
#: stop, `**`, `?` — is settled by `_file_named`.
_REFERENCE_MENTION = re.compile(r"references/[^\s`)\]>,;:'\"]+")
#: A word character in any script (`\w` is Unicode-aware; round 3 found the
#: ASCII class naming `references/日本` as `references`). The underscore is
#: punctuation here — markdown's italic marker — so `_references/g.md_` reads
#: as the path it wraps.
_WORD_CHAR = re.compile(r"[^\W_]")
_TRAILING_PUNCTUATION = re.compile(r"[\W_]+$")


def _file_named(mention: str, shipped: Collection[str]) -> str:
    """The file a mention in prose names: the shipped file it begins with, when
    what follows holds no word character (`**references/g.md**`,
    `references/g.md?`, `references/g.md.`) — the folder decides, so a shipped
    name may end in any character at all. A mention that matches no shipped
    file is shorn of trailing punctuation for the sentence that names it
    (round 1 stripped the full stop and nothing else, and `**references/g.md**`
    was refused for not shipping `references/g.md**`); the one name that
    sentence gets wrong is an unshipped file ending in `_`."""
    for rel in sorted(shipped, key=lambda r: len(r), reverse=True):
        if mention.startswith(rel) and not _WORD_CHAR.search(mention[len(rel) :]):
            return rel
    return _TRAILING_PUNCTUATION.sub("", mention)


def skill_name_problem(name: str) -> str | None:
    """Why ``name`` cannot be a skill hub entry's name, or ``None``.

    The rule is exactly "would the workspace loader list this folder": a name
    with a `/` installs as `.skill/a/b/`, two levels deep, which
    `workspace_skill_metas` never lists — so the install reply promised an
    index entry that could not exist (review round 2). Pinned by a parity
    table with the loader as oracle; `.dotted` and `with space` are listed
    there, so they pass here."""
    if "/" in name:
        return (
            f"`{name}` cannot be a skill name — a name is one folder under `.skill/` "
            "(no `/`), which is what the loader lists and `read_skill` loads"
        )
    if name in ("", ".", ".."):
        return f"`{name}` cannot be a skill name — it names no folder of its own under `.skill/`"
    return None


def skill_size_problem(sizes: Mapping[str, int]) -> str | None:
    """The caps, stated from sizes alone (a `stat`, never a read): an entry's
    files are read whole into memory on publish, stored outside any user
    quota, and every installer's workspace pays for them. The file count is
    the same check (G11)."""
    if len(sizes) > SKILL_HUB_MAX_FILES:
        return (
            f"the folder holds {len(sizes)} files, over the {SKILL_HUB_MAX_FILES} "
            "file cap for one skill hub entry — drop or bundle some"
        )
    total = sum(sizes.values())
    if total > SKILL_HUB_MAX_BYTES:
        return (
            f"the folder is {total / 2**20:.1f} MiB, over the {SKILL_HUB_MAX_BYTES // 2**20} MiB "
            "cap for one skill hub entry — drop or shrink the large files"
        )
    return None


def validate_skill_payload(folder: str, payload: Mapping[str, bytes]) -> list[str]:
    """The STRUCTURAL problems with a skill about to be published — every one,
    not the first, because the publisher reads this once in the chat and fixes
    what it names.

    These are the traps the workspace loader is tolerant of: it skips a skill
    that has any of them and logs a warning nobody reads, so a skill published
    with one is a skill no agent will ever load, with no error anywhere. That
    is the debug loop the hub's review exists to cut (plan D3, D4), and this
    half of it needs no model.
    """
    problems: list[str] = []
    raw = payload.get("SKILL.md")
    if raw is None:
        return ["no SKILL.md — a skill is a folder with a SKILL.md at its top level"]

    try:
        front, body = _parse_frontmatter(raw)
    except SkillError as exc:
        return [f"SKILL.md frontmatter does not parse: {exc}"]

    if (bad_name := skill_name_problem(folder)) is not None:
        problems.append(bad_name)
    name = str(front.get("name", "")).strip()
    if name != folder:
        problems.append(
            f"frontmatter `name: {name or '(missing)'}` must equal the folder name "
            f"`{folder}` — the loader keys the folder and skips a mismatch silently"
        )
    if not str(front.get("description", "")).strip():
        problems.append(
            "no `description` — the agent's index shows name and description only, "
            "so without one nothing the user says can match this skill"
        )
    if len(body) > SKILL_BODY_CAP:
        problems.append(
            f"body is {len(body)} characters; the loader refuses anything over {SKILL_BODY_CAP}"
        )

    if ".gitattributes" in payload:
        problems.append(
            "a top-level `.gitattributes` is reserved — the skill hub keeps its own there "
            "(one inside a subfolder is fine)"
        )

    # The size cap is not here: it is a rule over sizes, checked by the
    # publisher BEFORE the folder is read (`skill_size_problem`) and guaranteed
    # by `SkillHubStore.publish`, where the entry is made.
    named = {_file_named(m, payload) for m in _REFERENCE_MENTION.findall(body)}
    for mention in sorted(named):
        if mention not in payload:
            problems.append(
                f"the body names `{mention}` but the folder does not ship it — an agent "
                "following that reference will fail on first use"
            )

    for rel, data in sorted(payload.items()):
        if rel.startswith("scripts/") and rel.endswith(".py"):
            try:
                ast.parse(data.decode("utf-8", "replace"), filename=rel)
            except SyntaxError as exc:
                problems.append(f"`{rel}` does not parse: {exc.msg} (line {exc.lineno})")

    return problems


def nest_forks(hits: Mapping[str, SkillHubEntry]) -> list[tuple[str, list[str]]]:
    """``[(root_id, [fork_id, …]), …]`` over one listing's hits — the one
    nesting rule the page's list and the search tool share (plan Q4: 根在上、
    fork 收在原作底下).

    A hit nests under its parent only when the parent is itself shown as a
    root; everything else is a root of its own — a fork whose parent is out of
    view (filtered, unreadable, deleted) and a fork of a fork alike. One level,
    and nothing in ``hits`` is ever dropped: the first version skipped every
    fork-of-a-fork as "not a root" and then attached it to nothing.
    Roots and forks both keep ``hits``' order (``visible`` sorts by name then
    owner, so a listing built from it reads that way at both levels)."""

    def shown_as_root(entry_id: str) -> bool:
        entry = hits[entry_id]
        return entry.forked_from not in hits or not shown_as_root(entry.forked_from)

    # `shown_as_root` recurses up the parent chain; a chain is finite because
    # `forked_from` points at an entry published earlier, never at itself.
    roots = [i for i in hits if shown_as_root(i)]
    out: list[tuple[str, list[str]]] = []
    for root in roots:
        forks = [i for i, e in hits.items() if e.forked_from == root and not shown_as_root(i)]
        out.append((root, forks))
    return out


#: Tools `build_tools` grants a turn WITHOUT the App declaring them, so no
#: `agent.tools` list names them and a skill that mentions one must not be
#: told the App lacks it. Kept beside the one such grant in `agent/tools.py`
#: (`_grant_read_skill`) by the test that pins this constant against it.
IMPLICITLY_GRANTED_TOOLS = frozenset({"read_skill"})


def missing_tools_for(referenced: Collection[str], app_slug: str) -> list[str]:
    """The tools a skill mentions that `app_slug`'s ceiling does not grant —
    the install告知 (plan Q1/Q2): shown, never enforced. Against the App's
    declared ceiling, not a turn's effective set: the question is whether the
    App CAN follow the skill, which a per-item toggle does not change. An
    unknown App (a slug the catalog does not know) has no ceiling to compare
    against → nothing missing."""
    from .catalog import discover_app_slugs
    from .manifest import load_app_manifest

    if app_slug not in discover_app_slugs():
        return []
    ceiling = set(load_app_manifest(app_slug).agent.tools) | IMPLICITLY_GRANTED_TOOLS
    return [t for t in referenced if t not in ceiling]


def script_count(paths: Iterable[str]) -> int:
    """How many of a skill's files are scripts: those under its top-level
    ``scripts/`` folder, the skill convention. Not the executable bit — a
    version is committed with every file ``100644``, so it says nothing."""
    return sum(1 for p in paths if p.startswith("scripts/"))


def matches_query(entry: SkillHubEntry, query: str) -> bool:
    """The one search rule: the query, trimmed, is a case-insensitive
    substring of the name or the description; an empty query matches all.
    The page's list and the agent's `search_skill_hub` both call this — a copy
    kept alike by hand in each was how they were first written."""
    needle = query.strip().lower()
    return not needle or needle in entry.name.lower() or needle in entry.description.lower()


def skill_description(skill_md: str) -> str:
    """The frontmatter ``description`` of a SKILL.md — the line the entry lists
    under. For a SKILL.md that passed :func:`validate_skill_payload` this is
    non-empty; on one that did not, it is whatever is there (possibly "")."""
    try:
        front, _body = _parse_frontmatter(skill_md.encode())
    except SkillError:
        return ""
    return str(front.get("description", "")).strip()


def referenced_tools(skill_md: str, known: Collection[str]) -> list[str]:
    """The registered tool names the BODY mentions, sorted and unique.

    Whole words and code spans only — `exec` inside `executive` is not a call,
    and a substring match would tag half the hub with `exec`. The frontmatter is
    skipped: a description that says "uses exec" is describing, not calling.
    The registry is a parameter so this stays pure; the tool that publishes
    passes the platform's real one.
    """
    try:
        _front, body = _parse_frontmatter(skill_md.encode())
    except SkillError:
        body = skill_md
    words = set(re.findall(r"(?<![\w-])[A-Za-z_][\w]*(?![\w-])", body))
    return sorted(name for name in known if name in words)


def register_skill_hub(spec: SpecStar) -> None:
    """Idempotently register the model, post-``spec.apply`` so its auto-CRUD
    routes stay unemitted: publishing goes through the tool, which reviews
    first, and nothing may PUT a row around that."""
    with contextlib.suppress(ValueError):
        # `owner` + `name` are the identity `find` looks up; `forked_from` is
        # what the listing filters on ("roots only"). A filter on a non-indexed
        # field does not error — specstar warns and matches NOTHING, so `find`
        # would answer "absent" for every re-publish and mint a second row.
        spec.add_model(SkillHubEntry, indexed_fields=["owner", "name", "forked_from"])


class UnknownRevision(LookupError):
    """The revision is not one of this entry's versions."""


class VersionMoved(Exception):
    """The entry's current version is not the one the caller decided against —
    someone published or rolled back meanwhile (G6). Not retried: the owner
    chose looking at the version they saw."""


HistoryKind = Literal["publish", "rollback", "transfer", "permission"]


class HistoryEvent(Struct, kw_only=True):
    """One row of an entry's timeline (§8, G24): a revision that changed what
    the entry ships, who owns it, or who may see it."""

    revision: str
    kind: HistoryKind
    at: dt.datetime
    #: Who did it: the owner at the time — only an owner can do any of these
    #: (a first version moved in by the migration is the owner's too).
    by: str
    #: The owner after it (differs from `by` only on a transfer).
    owner: str
    #: The version current after this revision.
    commit: str
    description: str
    review_notes: list[str]
    #: On a rollback: the revision that first published the version brought back.
    to_revision: str = ""
    #: On a permission change: the visibility after it, and — when it is
    #: `restricted` — who may read it (`read_content`'s `user:` / `group:`
    #: subjects), G24's 「含名單」. Empty for private / public.
    visibility: str = ""
    audience: list[str] = field(default_factory=list)
    current: bool = False
    #: `v1`, `v2`, … — a publish or a rollback, counted oldest first; `None`
    #: on a row that changed no content (plan-skill-hub-ux-redo D8). Counted
    #: before the owner-only rows are dropped, so every reader says the same
    #: number for the same version.
    version: int | None = None


class FileChange(Struct, kw_only=True):
    """One file's difference between two versions. `patch` is a unified diff,
    or ``None`` for a file that is not text (LFS, or not UTF-8)."""

    path: str
    status: Literal["added", "removed", "changed"]
    patch: str | None


class MigrationReport(Struct, kw_only=True):
    """What `SkillHubStore.migrate_legacy` did: the entries it moved into git,
    and every `owner/name` held by more than one entry (left for the operator)."""

    migrated: list[str]
    duplicates: list[list[str]]
    #: Entries whose own top-level `.gitattributes` could not go into git
    #: (the skill hub's is there, W16): moved in without it.
    gitattributes_dropped: list[str] = field(default_factory=list)


def mint_entry_id() -> str:
    """A new entry's id. One function so the id's LENGTH is one fact: the
    publisher sizes the folder's `.origin` before the entry exists (a room
    check), and the manifest it sizes must be as long as the one it writes."""
    return uuid.uuid4().hex


class SkillHubStore:
    """Reads and writes skill hub entries and their versions. No policy here —
    who may publish, transfer or unpublish is the caller's question, checked
    against ``entry.owner`` and ``entry.permission`` before calling in."""

    def __init__(
        self,
        spec: SpecStar,
        repos: SkillHubRepos,
        *,
        legacy: FileStore | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
        pod: str | None = None,
    ) -> None:
        from .skill_hub_usage import UsageCounter, pod_name, register_skill_hub_usage

        self._spec = spec
        self.repos = repos
        # Post-apply, like the entries: the counts have no CRUD route (§3.2).
        register_skill_hub_usage(spec)
        #: Installs and uses, counted in this pod's memory (§4.8).
        self.usage = UsageCounter(
            spec, pod=pod or pod_name(), exists=lambda entry_id: self.get(entry_id) is not None
        )
        #: Where entries published before the git store keep their files.
        self._legacy = legacy
        self._sleep = sleep
        self._now = now
        self._sha256s: dict[tuple[str, str], dict[str, str]] = {}

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not exported
        return self._spec.get_resource_manager(SkillHubEntry)

    # ── read ─────────────────────────────────────────────────────────────

    def _row(self, entry_id: str) -> SkillHubEntry | None:
        try:
            data = self._rm().get(entry_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(data, SkillHubEntry)
        return data

    def get(self, entry_id: str) -> SkillHubEntry | None:
        """The entry, or ``None`` when it does not exist, was deleted, or is a
        first publish's draft that is not a version yet."""
        row = self._row(entry_id)
        return None if row is None or row.pending else row

    def can_read(self, entry: SkillHubEntry, viewer: str) -> bool:
        """Whether `viewer` may read the entry's content — the same `authorize`
        every other resource uses, with `entry.owner` (not `created_by`) as
        the owner because ownership is transferable here."""
        actor = Actor.human(viewer, groups=groups_of(self._spec, viewer))
        return authorize(actor, "read_content", entry.permission, created_by=entry.owner)

    def state_for(self, entry_id: str, viewer: str) -> tuple[UpstreamState, SkillHubEntry | None]:
        """The entry as `viewer` may know it: `("live", entry)`, or one of the
        two absent states with `None` — the caller shows a STATE for a copy
        whose upstream went away rather than raising into a listing."""
        entry = self.get(entry_id)
        if entry is None:
            return "deleted", None
        if not self.can_read(entry, viewer):
            return "unpublished", None
        return "live", entry

    def visible(self, viewer: str) -> list[tuple[str, SkillHubEntry]]:
        """Every entry `viewer` may read, as ``(id, entry)`` sorted by name then
        owner — what the page's list and the search tool both read, so the
        visibility rule is applied in exactly one place.

        A full read of the table, filtered in memory: the permission is not an
        indexed field (a scope on it would silently match nothing — the trap
        `register_skill_hub` names), and the hub holds one row per published
        skill, hundreds at most, read on a page open rather than in a turn.
        The viewer's groups are looked up ONCE for the whole list."""
        actor = Actor.human(viewer, groups=groups_of(self._spec, viewer))
        out: list[tuple[str, SkillHubEntry]] = []
        live = (QB.is_deleted() == False).build()  # noqa: E712 — specstar's meta predicate
        for res in self._rm().list_resources(live, returns=["info", "data"]):
            entry = res.data
            assert isinstance(entry, SkillHubEntry)
            if entry.pending:
                continue
            if authorize(actor, "read_content", entry.permission, created_by=entry.owner):
                out.append((res.info.resource_id, entry))
        out.sort(key=lambda pair: (pair[1].name, pair[1].owner))
        return out

    def forks_of(self, entry_id: str) -> list[str]:
        """Ids of the entries forked from `entry_id` — an indexed `forked_from`
        lookup, not a scan, tombstones and drafts excluded. Visibility is the
        caller's to apply."""
        query = ((QB["forked_from"] == entry_id) & (QB.is_deleted() == False)).build()  # noqa: E712
        return [
            res.info.resource_id
            for res in self._rm().list_resources(query, returns=["info", "data"])
            if isinstance(res.data, SkillHubEntry) and not res.data.pending
        ]

    def _same_name(self, owner: str, name: str) -> list[tuple[str, SkillHubEntry, dt.datetime]]:
        """Every live row for an identity — drafts included — with when it was made."""
        query = (
            (QB["owner"] == owner) & (QB["name"] == name) & (QB.is_deleted() == False)  # noqa: E712
        ).build()
        out: list[tuple[str, SkillHubEntry, dt.datetime]] = []
        for res in self._rm().list_resources(query, returns=["info", "data", "meta"]):
            assert isinstance(res.data, SkillHubEntry)
            out.append((res.info.resource_id, res.data, res.meta.created_time))
        return out

    def name_taken(self, owner: str, name: str) -> bool:
        """Whether `(owner, name)` is held — by an entry, or by a first publish
        in flight (a draft younger than `_DRAFT_TTL`). What a transfer must
        check: `find` skips drafts, and a transfer landing beside one left two
        live rows for one name (review round 2)."""
        return any(
            not row.pending or self._now() - made <= _DRAFT_TTL
            for _id, row, made in self._same_name(owner, name)
        )

    def find(self, owner: str, name: str) -> str | None:
        """The entry id for an identity, or ``None``. Scoped by both indexed
        fields, so this is a point lookup rather than a scan."""
        # Tombstones excluded: a deleted entry's id must NOT be the one a
        # re-publish lands on — the copies pointing at it read "deleted" for
        # good (Q10), and the re-publish is a new entry. Drafts excluded: a
        # first publish in flight is not an entry yet.
        for entry_id, row, _made in self._same_name(owner, name):
            if not row.pending:
                return entry_id
        return None

    async def _files(
        self, entry_id: str, entry: SkillHubEntry, paths: Collection[str] | None = None
    ) -> dict[str, bytes]:
        if entry.commit:
            return await self.repos.read(entry_id, entry.commit, paths=paths)
        # Published before the git store: its files are where they were written.
        if self._legacy is None:
            return {}
        ns = entry.blobs or _BLOB_PREFIX + entry_id
        out: dict[str, bytes] = {}
        for path in await self._legacy.ls(ns):
            rel = path.lstrip("/")
            if paths is None or rel in paths:
                out[rel] = await self._legacy.read(ns, path)
        return out

    async def skill_md_of(self, entry_id: str) -> bytes:
        """The entry's ``SKILL.md`` alone — what a page that shows the skill
        and LISTS the other files needs."""
        entry = self.get(entry_id)
        assert entry is not None  # the caller resolved it a moment ago
        return (await self._files(entry_id, entry, ["SKILL.md"]))["SKILL.md"]

    async def file_sizes(
        self, entry_id: str, commit: str | None = None
    ) -> list[tuple[str, int | None]]:
        """``(path, size)`` for every file of a version — the current one
        unless `commit` names another — sorted by path, without reading them:
        the tree says each size, an LFS file's being its content's. An entry
        not in git yet records only its paths, so its sizes are ``None``."""
        if commit is None:
            entry = self.get(entry_id)
            assert entry is not None
            if not entry.commit:
                return [(path, None) for path in sorted(entry.origin.files)]
            commit = entry.commit
        tree = await self.repos.tree(entry_id, commit)
        return [
            (path, f.lfs[1] if f.lfs is not None else f.size) for path, f in sorted(tree.items())
        ]

    def copy_manifest(self, entry_id: str, entry: SkillHubEntry) -> SkillOrigin:
        """What a copy of the entry's current version records in its `.origin`
        (G12): the commit — or, for an entry not yet in git, the per-file
        sha256 it was published with."""
        if entry.commit:
            return SkillOrigin(source="hub", files={}, entry=entry_id, commit=entry.commit)
        return SkillOrigin(source="hub", files=dict(entry.origin.files), entry=entry_id)

    async def file_sha256s(self, entry_id: str) -> dict[str, str]:
        """The current version as the per-file sha256 map a copy made before the
        git store carries (G17) — computed once per version, since a Skills
        panel asks it for every such copy on every open."""
        entry = self.get(entry_id)
        assert entry is not None and entry.commit
        key = (entry_id, entry.commit)
        if key not in self._sha256s:
            tree = await self.repos.tree(entry_id, entry.commit)
            plain = [p for p, f in tree.items() if f.lfs is None]
            data = await self.repos.read(entry_id, entry.commit, paths=plain) if plain else {}
            shas = {p: f.lfs[0] for p, f in tree.items() if f.lfs is not None}
            shas.update({p: hashlib.sha256(b).hexdigest() for p, b in data.items()})
            if len(self._sha256s) >= _SHA256_CACHE:
                self._sha256s.pop(next(iter(self._sha256s)))
            self._sha256s[key] = shas
        return dict(self._sha256s[key])

    async def payload_of(self, entry_id: str) -> dict[str, bytes]:
        """Every file of the version the ROW points at, keyed like
        :func:`skill_payload` keys them — the shape ``materialize_skill``
        writes into a workspace."""
        entry = self.get(entry_id)
        assert entry is not None
        return await self._files(entry_id, entry)

    # ── write ────────────────────────────────────────────────────────────

    async def _change(
        self,
        entry_id: str,
        change: Callable[[SkillHubEntry], SkillHubEntry | None],
        *,
        current_commit: str | None = None,
    ) -> SkillHubEntry | None:
        """Apply ONE writer's change to the row as it is now, and tag the new
        revision at the version it records. Returns the row written, or
        ``None`` when the change no longer applies.

        Every writer goes through here (review round 1, defect #3). Reading the
        whole row, awaiting git, and writing it back let a publish undo an
        unpublish and two publishes leave the row behind master. So the write
        is a compare-and-swap on the revision read: a writer that lost it
        reads again and re-applies only its own change. `current_commit` is
        for a writer recording a version: it records it only while that
        version is still master — once another has moved master on, recording
        that one is the other writer's job, and doing it here would put the
        row behind master."""
        rm = self._rm()
        for _attempt in range(_WRITE_TRIES):
            try:
                res = rm.get(entry_id)
            except (ResourceIDNotFoundError, ResourceIsDeletedError):
                return None  # deleted meanwhile: nothing left to record on
            row = res.data
            assert isinstance(row, SkillHubEntry)
            # AFTER the read (review round 2): a writer that finishes between
            # this check and the CAS below changed the revision just read, so
            # the CAS fails and the next attempt sees its master.
            if current_commit is not None and await self.repos.master(entry_id) != current_commit:
                return None
            new = change(row)
            if new is None:
                return None
            try:
                info = rm.update(entry_id, new, expected_revision_id=res.info.revision_id)
            except PreconditionFailedError:
                continue
            except ResourceIsDeletedError:
                return None
            if new.commit:
                await self.repos.tag(entry_id, info.revision_id, new.commit)
            return new
        raise ValueError("the skill hub entry is being changed by someone else — try again")

    # The management writes (plan P7). No policy here either — the route has
    # already established the caller is the owner. Each replaces ONE field
    # and carries every other one over, because `update` is a whole-row write.

    async def set_permission(self, entry_id: str, permission: Permission) -> None:
        """Visibility + grant lists. Unpublish is `visibility="private"` with
        the lists kept, so a later republish loses no invite."""
        await self._change(
            entry_id, lambda row: msgspec.structs.replace(row, permission=permission)
        )

    async def transfer(self, entry_id: str, owner: str) -> None:
        """Move `owner` and nothing else. The id is the identity every copy's
        `.origin` and every fork's `forked_from` point at, so they all survive
        a transfer untouched. The caller has checked `(owner, name)` is free."""
        await self._change(entry_id, lambda row: msgspec.structs.replace(row, owner=owner))

    # ── history (§8) ─────────────────────────────────────────────────────

    async def history(self, entry_id: str, *, viewer: str) -> list[HistoryEvent]:
        """The timeline, newest first. A version is a publish the first time
        its commit appears and a rollback when it comes back; revisions that
        changed nothing a person asks about (a first publish's draft, the
        tag-only writes) are not rows. Permission changes are the owner's
        alone (G24). Also repairs the one tag a crash between the row write
        and the tag can leave missing — the current revision's (G8)."""
        rm = self._rm()

        def revisions() -> list[tuple[str, dt.datetime, SkillHubEntry]]:
            out = []
            for revision_id in rm.list_revisions(entry_id):
                res = rm.get_resource_revision(entry_id, revision_id)
                if isinstance(res.data, SkillHubEntry):
                    out.append((revision_id, res.info.created_time, res.data))
            return out

        events: list[HistoryEvent] = []
        numbered = 0
        first_seen: dict[str, str] = {}
        prev: SkillHubEntry | None = None
        last_revision = ""
        # One read per revision: off the loop, like every other store read here.
        for revision_id, made, row in await asyncio.to_thread(revisions):
            if row.pending or not row.commit:
                continue
            last_revision = revision_id

            def event(
                kind: HistoryKind,
                by: str,
                *,
                to_revision: str = "",
                visibility: str = "",
                audience: list[str] | None = None,
                revision_id: str = revision_id,
                at: dt.datetime = made,
                row: SkillHubEntry = row,
            ) -> HistoryEvent:
                return HistoryEvent(
                    revision=revision_id,
                    kind=kind,
                    at=at,
                    by=by,
                    owner=row.owner,
                    commit=row.commit,
                    description=row.description,
                    review_notes=list(row.review.notes),
                    to_revision=to_revision,
                    visibility=visibility,
                    audience=list(audience or []),
                )

            if prev is None or row.commit != prev.commit:
                if row.commit in first_seen:
                    events.append(event("rollback", row.owner, to_revision=first_seen[row.commit]))
                else:
                    first_seen[row.commit] = revision_id
                    events.append(event("publish", row.owner))
                numbered += 1
                events[-1].version = numbered
            elif row.owner != prev.owner:
                events.append(event("transfer", prev.owner))
            elif row.permission != prev.permission:
                events.append(
                    event(
                        "permission",
                        row.owner,
                        visibility=row.permission.visibility,
                        # The lists only mean something while restricted: a
                        # private or public row names nobody (round 3).
                        audience=list(row.permission.read_content)
                        if row.permission.visibility == "restricted"
                        else [],
                    )
                )
            prev = row
        if prev is not None and last_revision not in await self.repos.tagged_revisions(entry_id):
            await self.repos.tag(entry_id, last_revision, prev.commit)
        current = self.get(entry_id)
        if current is None or current.owner != viewer:
            events = [e for e in events if e.kind != "permission"]
        # After the owner-only rows are dropped, so every reader has one.
        if events:
            events[-1].current = True
        events.reverse()
        return events

    async def version(self, entry_id: str, revision_id: str) -> SkillHubEntry:
        """The entry as it was at `revision_id`: its fields, and through
        `commit`, its files. Only revisions that name a version."""
        try:
            row = self._rm().get_resource_revision(entry_id, revision_id).data
        except (KeyError, RevisionNotFoundError) as e:
            raise UnknownRevision(revision_id) from e
        if not isinstance(row, SkillHubEntry) or row.pending or not row.commit:
            raise UnknownRevision(revision_id)
        return row

    async def diff(self, entry_id: str, from_revision: str, to_revision: str) -> list[FileChange]:
        """What changed from one version to another, per file. Trees are
        listed, and only the changed text files are read."""
        a = (await self.version(entry_id, from_revision)).commit
        b = (await self.version(entry_id, to_revision)).commit
        old, new = await self.repos.tree(entry_id, a), await self.repos.tree(entry_id, b)
        changed = sorted(
            p
            for p in old.keys() | new.keys()
            if p not in old or p not in new or old[p].blob_id != new[p].blob_id
        )
        texts_a = (
            await self.repos.read(
                entry_id,
                a,
                paths=[
                    p
                    for p in changed
                    if p in old and old[p].lfs is None and old[p].size <= DIFF_TEXT_CAP
                ],
            )
            if changed
            else {}
        )
        texts_b = (
            await self.repos.read(
                entry_id,
                b,
                paths=[
                    p
                    for p in changed
                    if p in new and new[p].lfs is None and new[p].size <= DIFF_TEXT_CAP
                ],
            )
            if changed
            else {}
        )
        textual = [p for p in changed if _is_text(old.get(p), new.get(p), texts_a, texts_b, p)]
        # git computes them, a few at a time: one subprocess per file.
        gate = asyncio.Semaphore(_DIFF_PARALLEL)

        async def patch_of(path: str) -> tuple[str, str | None]:
            async with gate:
                try:
                    return path, await self.repos.diff_text(entry_id, a, b, path)
                except GitError:
                    # One file git could not diff shows no lines; the rest
                    # of the comparison still answers.
                    logger.warning("skill hub: no diff for %s in %s", path, entry_id)
                    return path, None

        patches: dict[str, str | None] = dict.fromkeys(changed)
        patches.update(dict(await asyncio.gather(*(patch_of(p) for p in textual))))
        out: list[FileChange] = []
        for path in changed:
            status: Literal["added", "removed", "changed"] = (
                "added" if path not in old else "removed" if path not in new else "changed"
            )
            out.append(FileChange(path=path, status=status, patch=patches[path]))
        return out

    async def rollback(self, entry_id: str, revision_id: str, *, expected: str) -> str:
        """Make the version `revision_id` was current in the current one (§4.3).

        `expected` is the commit the owner was looking at; master moves only
        from it, so a version published since is refused rather than silently
        discarded. The row takes the OLD revision's content fields — `commit`,
        `description`, `review`, `referenced_tools` — and keeps today's owner and
        permission: specstar's `switch` would bring those back too (G18). No new
        review: that version was reviewed when it was published (G20)."""
        current = self.get(entry_id)
        assert current is not None  # the route resolved it a moment ago
        try:
            old = self._rm().get_resource_revision(entry_id, revision_id).data
        # The memory and postgres stores raise `KeyError`; the documented one is
        # `RevisionNotFoundError`. Either means the entry has no such revision.
        except (KeyError, RevisionNotFoundError) as e:
            raise UnknownRevision(revision_id) from e
        if not isinstance(old, SkillHubEntry) or not old.commit:
            # A draft, or a revision written before the entry was moved into git.
            raise UnknownRevision(revision_id)
        if old.commit == current.commit:
            # Nothing to move — but only if the version the owner saw is still
            # the current one; otherwise they decided against a page that is gone.
            if await self.repos.master(entry_id) != expected:
                raise VersionMoved(entry_id)
            return current.commit
        # The lease is the one check: master is written before the row, so a
        # row never names a commit master has not reached.
        try:
            moved = await self.repos.move_master(entry_id, old.commit, expected=expected)
        except GitError:  # not a commit id at all: not the version the page showed
            raise VersionMoved(entry_id) from None
        if not moved:
            raise VersionMoved(entry_id)
        written = await self._change(
            entry_id,
            lambda row: msgspec.structs.replace(
                row,
                commit=old.commit,
                description=old.description,
                review=old.review,
                referenced_tools=list(old.referenced_tools),
                content_at=self._now(),
            ),
            current_commit=old.commit,
        )
        if written is None:
            # Not recorded: the entry was deleted meanwhile (round 3), or a
            # publish moved master on — which is the current version now.
            if self.get(entry_id) is None:
                raise UnknownRevision(revision_id)
            raise VersionMoved(entry_id)
        return old.commit

    async def migrate_legacy(self) -> MigrationReport:
        """Move every entry published before the git store into it (§6, G10):
        its own repo, a first commit of the files its `blobs` namespace holds,
        the row naming that commit, its revision tagged.

        Safe to run again and on several pods at once: an entry with a commit
        is skipped, and the first version goes in through the same lease a
        publish uses — a repo whose master is already set (another runner, or
        a run that died before writing the row) is ADOPTED, never given a
        second first version. Two live entries with one `owner/name` are
        reported for the operator; nothing is merged."""
        live = (QB.is_deleted() == False).build()  # noqa: E712 — specstar's meta predicate
        rows = [
            (res.info.resource_id, res.data)
            for res in self._rm().list_resources(live, returns=["info", "data"])
            if isinstance(res.data, SkillHubEntry) and not res.data.pending
        ]
        dropped: list[str] = []
        migrated = [
            entry_id
            for entry_id, row in sorted(rows, key=lambda pair: pair[0])
            if not row.commit and await self._ensure_in_git(entry_id, row, dropped=dropped)
        ]
        by_name: dict[tuple[str, str], list[str]] = {}
        for entry_id, row in rows:
            by_name.setdefault((row.owner, row.name), []).append(entry_id)
        duplicates = sorted(sorted(ids) for ids in by_name.values() if len(ids) > 1)
        return MigrationReport(
            migrated=migrated, duplicates=duplicates, gitattributes_dropped=dropped
        )

    async def _ensure_in_git(
        self, entry_id: str, row: SkillHubEntry, *, dropped: list[str] | None = None
    ) -> bool:
        """Give an entry published before the git store its first version: the
        files its `blobs` namespace holds. A repo whose master is already set
        (another runner, or a run that died before writing the row) is
        adopted, never given a second first version. Returns whether this call
        recorded it on the row."""
        payload = await self._files(entry_id, row)
        if payload.pop(".gitattributes", None) is not None and dropped is not None:
            dropped.append(entry_id)
        commit = await self.repos.write_version(
            entry_id, payload, parent=None, author=row.owner, message="migrated"
        )
        if not await self.repos.move_master(entry_id, commit, expected=None):
            adopted = await self.repos.master(entry_id)
            assert adopted is not None  # the lease only fails when master is set
            commit = adopted
        # A publish may have recorded a version since: it wins, untouched.
        written = await self._change(
            entry_id,
            lambda now: None if now.commit else msgspec.structs.replace(now, commit=commit),
        )
        return written is not None

    async def delete(self, entry_id: str) -> None:
        """Soft-delete the row. Final: `state_for` answers `deleted` for every
        copy and fork from now on, and a re-publish of the name is a NEW entry
        (`find` skips tombstones). The version history is KEPT (§7 Q2)."""
        self._rm().delete(entry_id)

    async def _claim_name(self, row: SkillHubEntry) -> str:
        """Make a first publish's pending draft and keep it only when no other
        live row has the same `(owner, name)` (G26): the later of two first
        publishes always sees the earlier, so two can never both stay; when both
        step aside, the random wait lets the retries come one after the other.
        A draft older than `_DRAFT_TTL` is a dead publisher's and is cleared
        (§7 Q3). Returns the draft's id."""
        rm = self._rm()
        for attempt in range(_FIRST_PUBLISH_TRIES):
            entry_id = mint_entry_id()
            rm.create(msgspec.structs.replace(row, pending=True), resource_id=entry_id)
            others = []
            for other_id, other, made in self._same_name(row.owner, row.name):
                if other_id == entry_id:
                    continue
                if other.pending and self._now() - made > _DRAFT_TTL:
                    with contextlib.suppress(ResourceIDNotFoundError):
                        rm.permanently_delete(other_id)
                    continue
                others.append(other_id)
            if not others:
                return entry_id
            rm.permanently_delete(entry_id)
            await self._sleep(random.uniform(0.05, 0.25) * (attempt + 1))  # noqa: S311
        raise ValueError(
            f"someone is publishing a skill with the same name ({row.name!r}) right now "
            "— try again in a moment"
        )

    async def publish(
        self,
        *,
        owner: str,
        name: str,
        description: str,
        source_item: str,
        source_app: str,
        source_profile: str,
        payload: Mapping[str, bytes],
        referenced_tools: list[str],
        review: SkillHubReview,
        forked_from: str = "",
    ) -> str:
        """Create the entry for ``(owner, name)``, or — when it already exists —
        commit the new version on top of master and write a new revision of the
        same row (§4.1, §4.2). Returns the entry id either way.

        The version is written to git first and master is moved by a leased
        push (the lock, G5); the row follows. A first publish makes its row as
        a pending draft BEFORE anything else, so the name can be checked; a
        failure after that removes the draft, and a draft is invisible to
        every reader until its version is in git.
        """
        # The publisher was refused with a sentence for these before the review
        # ran; the row is made HERE, so here they are guaranteed for any caller.
        for problem in (
            skill_name_problem(name),
            skill_size_problem({rel: len(data) for rel, data in payload.items()}),
        ):
            if problem is not None:
                raise ValueError(problem)
        existing = self.find(owner, name)
        row = SkillHubEntry(
            owner=owner,
            name=name,
            description=description,
            source_item=source_item,
            source_app=source_app,
            source_profile=source_profile,
            review=review,
            forked_from=forked_from,
            referenced_tools=list(referenced_tools),
        )
        if existing is None:
            entry_id = await self._claim_name(row)
            try:
                commit = await self.repos.write_version(
                    entry_id, payload, parent=None, author=owner, message=f"publish {name}"
                )
                if not await self.repos.move_master(entry_id, commit, expected=None):
                    raise RuntimeError(f"a new entry's repo already had a version: {entry_id}")
            except BaseException:
                with contextlib.suppress(ResourceIDNotFoundError):
                    self._rm().permanently_delete(entry_id)
                raise
            # The draft is this call's own row: nobody else writes it — but it
            # can be cleared as a dead publisher's (round 3).
            if (
                await self._change(
                    entry_id,
                    lambda draft: msgspec.structs.replace(
                        row, commit=commit, pending=False, content_at=self._now()
                    ),
                )
                is None
            ):
                raise ValueError(_VANISHED.format(name=name))
            return entry_id

        current = self.get(existing)
        assert current is not None  # `find` answered it
        if not current.commit:
            # Published before the git store and not migrated yet: its version
            # goes in first, so the new one has it as parent (round 1,
            # conformance #3) — otherwise the migration would skip a row that
            # already has a commit, and the old version would be gone.
            dropped: list[str] = []
            await self._ensure_in_git(existing, current, dropped=dropped)
            if dropped:
                logger.warning(
                    "skill hub: %s moved into git without its own top-level .gitattributes",
                    existing,
                )
        commit = await self._commit_on_master(existing, payload, author=owner, name=name)
        written = await self._change(
            existing,
            lambda now: msgspec.structs.replace(
                now,
                description=row.description,
                source_item=row.source_item,
                source_app=row.source_app,
                source_profile=row.source_profile,
                review=row.review,
                referenced_tools=row.referenced_tools,
                commit=commit,
                content_at=self._now(),
            ),
            current_commit=commit,
        )
        # `None` either way: another publish moved master on (its version is
        # current, this one stays in git), or the entry was deleted meanwhile.
        if written is None and self.get(existing) is None:
            raise ValueError(_VANISHED.format(name=name))
        return existing

    async def _commit_on_master(
        self, entry_id: str, payload: Mapping[str, bytes], *, author: str, name: str
    ) -> str:
        """Commit `payload` on top of master and move master to it, re-reading
        and re-committing when another publish moved master first (G5)."""
        for _attempt in range(_FIRST_PUBLISH_TRIES):
            parent = await self.repos.master(entry_id)
            commit = await self.repos.write_version(
                entry_id, payload, parent=parent, author=author, message=f"publish {name}"
            )
            if await self.repos.move_master(entry_id, commit, expected=parent):
                return commit
        raise ValueError(
            f"{name!r} is being published by someone else right now — try again in a moment"
        )


#: A text file larger than this (on either side) is compared, not diffed
#: (plan-skill-hub-history W15, the user's call): the patch is shown in a page.
DIFF_TEXT_CAP = 256 * 1024


#: How many `git diff` subprocesses one comparison runs at once.
_DIFF_PARALLEL = 8


def _is_text(
    old: TreeFile | None,
    new: TreeFile | None,
    texts_a: Mapping[str, bytes],
    texts_b: Mapping[str, bytes],
    path: str,
) -> bool:
    """Whether a changed file gets a line diff: not LFS, within the cap on
    both sides, and UTF-8 on both sides."""
    if (old is not None and old.lfs is not None) or (new is not None and new.lfs is not None):
        return False
    if any(f is not None and f.size > DIFF_TEXT_CAP for f in (old, new)):
        return False
    try:
        if old is not None:
            texts_a[path].decode()
        if new is not None:
            texts_b[path].decode()
    except UnicodeDecodeError:
        return False
    return True

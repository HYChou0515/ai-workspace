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
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

import msgspec
from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..perm import Actor, Permission, authorize
from ..resources.groups import groups_of
from .skill_payload import SkillOrigin
from .skills import SKILL_BODY_CAP, SkillError, _parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from ..filestore.protocol import FileStore
    from .skill_hub_git import SkillHubRepos

#: What an entry is to one viewer. `live`: readable. `unpublished`: exists but
#: this viewer may not read it (the owner made it private / restricted them out
#: — Q5). `deleted`: soft-deleted or never existed — from outside there is no
#: difference (Q10).
UpstreamState = Literal["live", "unpublished", "deleted"]

#: The FileStore namespace an entry's files live under: a synthetic workspace
#: id per PUBLISHED VERSION (`skill-hub:<entry id>:<version>`), so `purge` on a
#: version takes exactly its files, and a version being written never shares a
#: namespace with the one the row still points at (review round 1, finding A).
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
    """One published skill. Files live beside it as blobs; see module docstring."""

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
    #: The FileStore namespace holding THIS version's files. Per version, not
    #: per entry: a re-publish writes the next version into a fresh namespace,
    #: moves the row here, and only then drops the previous one — so the row
    #: never points at a namespace being emptied or half-filled. `""` is the
    #: pre-versioned layout (`skill-hub:<id>`), kept decodable.
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
    ) -> None:
        self._spec = spec
        self.repos = repos
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

    async def file_names(self, entry_id: str) -> list[str]:
        """The files of the current version, without reading them."""
        entry = self.get(entry_id)
        assert entry is not None
        if entry.commit:
            return sorted(await self.repos.tree(entry_id, entry.commit))
        return sorted(entry.origin.files)

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

    def _update(self, entry_id: str, row: SkillHubEntry) -> str:
        """Write a revision; return its id (what the version's tag is named after)."""
        return self._rm().update(entry_id, row).revision_id

    async def _tag(self, entry_id: str, revision_id: str, commit: str) -> None:
        if commit:
            await self.repos.tag(entry_id, revision_id, commit)

    async def _manage(self, entry_id: str, row: SkillHubEntry) -> None:
        """A revision that changes who or how, not what: master does not move,
        and the revision is tagged at the version it is current in (§4.4)."""
        revision = self._update(entry_id, row)
        await self._tag(entry_id, revision, row.commit)

    # The management writes (plan P7). No policy here either — the route has
    # already established the caller is the owner. Each replaces ONE field
    # and carries every other one over, because `update` is a whole-row write.

    async def set_permission(self, entry_id: str, permission: Permission) -> None:
        """Visibility + grant lists. Unpublish is `visibility="private"` with
        the lists kept, so a later republish loses no invite."""
        current = self.get(entry_id)
        assert current is not None  # the route resolved it a moment ago
        await self._manage(entry_id, msgspec.structs.replace(current, permission=permission))

    async def transfer(self, entry_id: str, owner: str) -> None:
        """Move `owner` and nothing else. The id is the identity every copy's
        `.origin` and every fork's `forked_from` point at, so they all survive
        a transfer untouched. The caller has checked `(owner, name)` is free."""
        current = self.get(entry_id)
        assert current is not None
        await self._manage(entry_id, msgspec.structs.replace(current, owner=owner))

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
            final = msgspec.structs.replace(row, commit=commit)
            await self._tag(entry_id, self._update(entry_id, final), commit)
            return entry_id

        current = self.get(existing)
        assert current is not None  # `find` answered it
        commit = await self._commit_on_master(existing, payload, author=owner, name=name)
        final = msgspec.structs.replace(
            row,
            owner=current.owner,
            # Set once, when the fork is born. A re-publish does not know (or
            # pass) where the fork came from; the row does.
            forked_from=current.forked_from,
            permission=current.permission,
            commit=commit,
            blobs=current.blobs,
        )
        await self._tag(existing, self._update(existing, final), commit)
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

"""The skill hub — skills flow between people without the operator's git.

A skill a user wrote lives in ONE workspace (``.skill/<name>/``). Until now the
only way to hand it to somebody else was for the operator to copy it into
``sample-skills/`` and redeploy — every share an ops ticket. This module is the
fourth source ``read_skill`` can materialize from, and the one users fill
themselves. Design and the ten decisions behind it: ``docs/plan-skill-hub.md``.

Storage is two halves, deliberately:

* one specstar row per published skill (:class:`SkillHubEntry`) — the metadata
  everything else keys on, with specstar's own revisions standing in for
  version history;
* the files as blobs in the FileStore under a per-entry namespace, because a
  ``references/`` folder or a ``scripts/`` folder can be large and a struct is
  not the place for bytes.

Identity is ``(owner, name)`` over the row's stable resource id. Installed
copies and forks point at the ID, so transferring ownership — an explicit,
mutable ``owner`` field rather than ``created_by`` — breaks nothing downstream.
"""

from __future__ import annotations

import ast
import contextlib
import re
import uuid
from typing import TYPE_CHECKING, Literal

import msgspec
from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..perm import Actor, Permission, authorize
from ..resources.groups import groups_of
from .skill_payload import SkillOrigin, origin_for
from .skills import SKILL_BODY_CAP, SkillError, _parse_frontmatter

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from ..filestore.protocol import FileStore

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
    #: `origin_for("hub", payload, entry=<this id>)` — the SAME manifest an
    #: installed copy writes to its `.origin`, computed by the same function.
    #: `skill_upstream` compares the two, hash for hash, WITHOUT reading the
    #: files back; two hash implementations kept alike by hand would diverge
    #: the moment one was edited.
    origin: SkillOrigin
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


# ── what publishing checks ───────────────────────────────────────────────────

#: A `references/...` path the body names. Stops at whitespace or the markdown
#: punctuation that ends a path in prose — the closing backtick, a bracket, a
#: comma or a full stop.
_REFERENCE_MENTION = re.compile(r"references/[^\s`)\]>,;:'\"]+")


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

    total = sum(len(data) for data in payload.values())
    if total > SKILL_HUB_MAX_BYTES:
        problems.append(
            f"the folder is {total / 2**20:.1f} MiB, over the {SKILL_HUB_MAX_BYTES // 2**20} MiB "
            "cap for one skill hub entry — drop or shrink the large files"
        )
    # A mention at the end of a sentence carries its full stop into the match
    # (the class excludes the other punctuation but a dot is a path character);
    # a file name never ends in one, so trailing dots are the sentence's.
    for mention in sorted({m.rstrip(".") for m in _REFERENCE_MENTION.findall(body)}):
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


class SkillHubStore:
    """Reads and writes hub entries and their files. No policy here — who may
    publish, transfer or unpublish is the caller's question, checked against
    ``entry.owner`` and ``entry.permission`` before calling in."""

    def __init__(self, spec: SpecStar, blobs: FileStore) -> None:
        self._spec = spec
        self._blobs = blobs

    def _rm(self):  # noqa: ANN202 — specstar's manager type is not exported
        return self._spec.get_resource_manager(SkillHubEntry)

    # ── read ─────────────────────────────────────────────────────────────

    def get(self, entry_id: str) -> SkillHubEntry | None:
        """The entry, or ``None`` when it does not exist or was deleted."""
        try:
            data = self._rm().get(entry_id).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(data, SkillHubEntry)
        return data

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
            if authorize(actor, "read_content", entry.permission, created_by=entry.owner):
                out.append((res.info.resource_id, entry))
        out.sort(key=lambda pair: (pair[1].name, pair[1].owner))
        return out

    def forks_of(self, entry_id: str) -> list[str]:
        """Ids of the entries forked from `entry_id` — an indexed `forked_from`
        lookup, not a scan, tombstones excluded. Visibility is the caller's
        to apply."""
        query = ((QB["forked_from"] == entry_id) & (QB.is_deleted() == False)).build()  # noqa: E712
        return [res.info.resource_id for res in self._rm().list_resources(query, returns=["info"])]

    def find(self, owner: str, name: str) -> str | None:
        """The entry id for an identity, or ``None``. Scoped by both indexed
        fields, so this is a point lookup rather than a scan."""
        # Tombstones excluded: `list_resources` returns soft-deleted rows, and
        # a deleted entry's id must NOT be the one a re-publish lands on — the
        # copies pointing at it read "deleted" for good (Q10), and the
        # re-publish is a new entry.
        query = (
            (QB["owner"] == owner) & (QB["name"] == name) & (QB.is_deleted() == False)  # noqa: E712
        ).build()
        for res in self._rm().list_resources(query, returns=["info"]):
            return res.info.resource_id
        return None

    @staticmethod
    def _namespace(entry_id: str, entry: SkillHubEntry | None) -> str:
        return (entry.blobs if entry is not None and entry.blobs else "") or _BLOB_PREFIX + entry_id

    async def payload_of(self, entry_id: str) -> dict[str, bytes]:
        """Every file the entry ships, keyed like :func:`skill_payload` keys
        them — the shape ``materialize_skill`` writes into a workspace. Reads
        the version the ROW points at; a version being published is invisible
        until the row moves to it."""
        ws = self._namespace(entry_id, self.get(entry_id))
        out: dict[str, bytes] = {}
        for path in await self._blobs.ls(ws):
            out[path.lstrip("/")] = await self._blobs.read(ws, path)
        return out

    # ── write ────────────────────────────────────────────────────────────

    # The management writes (plan P7). No policy here either — the route has
    # already established the caller is the owner. Each replaces ONE field
    # and carries every other one over, because `update` is a whole-row write.

    def set_permission(self, entry_id: str, permission: Permission) -> None:
        """Visibility + grant lists. Unpublish is `visibility="private"` with
        the lists kept, so a later republish loses no invite."""
        current = self.get(entry_id)
        assert current is not None  # the route resolved it a moment ago
        self._rm().update(entry_id, msgspec.structs.replace(current, permission=permission))

    def transfer(self, entry_id: str, owner: str) -> None:
        """Move `owner` and nothing else. The id is the identity every copy's
        `.origin` and every fork's `forked_from` point at, so they all survive
        a transfer untouched. The caller has checked `(owner, name)` is free."""
        current = self.get(entry_id)
        assert current is not None
        self._rm().update(entry_id, msgspec.structs.replace(current, owner=owner))

    async def delete(self, entry_id: str) -> None:
        """Soft-delete the row and free its files. Final: `state_for` answers
        `deleted` for every copy and fork from now on, and a re-publish of the
        name is a NEW entry (`find` skips tombstones). No restore (Q5)."""
        ws = self._namespace(entry_id, self.get(entry_id))
        self._rm().delete(entry_id)
        await self._blobs.purge(ws)

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
        replace its files and write a new revision of the same row. Returns the
        entry id either way, so a caller cannot tell the two apart and does not
        need to: the id is the identity that survives.

        Every version gets its own namespace. The files are written there
        FIRST, the row moves to it SECOND, and the previous version's namespace
        is dropped LAST — so a row that exists always describes files that
        exist, on the first publish and on every re-publish alike. The first
        version purged the old files before writing the new ones, and a failure
        in between left a LIVE row whose manifest named files its namespace no
        longer held (review round 1). A crash now leaves orphan blobs in a
        namespace no row points at, never a row pointing at nothing; those
        orphans are the accepted residue.
        """
        existing = self.find(owner, name)
        # A NEW entry's id is minted here, not by `create`, so the files can be
        # written under it before the row exists. Asking `create` for the id
        # first would put the row before the files, and a crash in between
        # would leave a row pointing at nothing: the one shape this rules out.
        entry_id = existing if existing is not None else uuid.uuid4().hex
        current = self.get(entry_id) if existing is not None else None

        # Replace, not merge — and in a FRESH namespace: a file the new version
        # dropped must not survive from the old one (an installed copy of the
        # new version would carry a reference the SKILL.md no longer makes),
        # and the old version must stay whole until the row has moved.
        ws = f"{_BLOB_PREFIX}{entry_id}:{uuid.uuid4().hex}"
        for rel, data in payload.items():
            await self._blobs.write(ws, f"/{rel}", data)

        origin = origin_for("hub", payload, entry=entry_id)
        if existing is None:
            self._rm().create(
                SkillHubEntry(
                    owner=owner,
                    name=name,
                    description=description,
                    source_item=source_item,
                    source_app=source_app,
                    source_profile=source_profile,
                    origin=origin,
                    review=review,
                    forked_from=forked_from,
                    referenced_tools=list(referenced_tools),
                    blobs=ws,
                ),
                resource_id=entry_id,
            )
            return entry_id

        assert current is not None  # `find` answered it, and it is not a tombstone
        previous = self._namespace(entry_id, current)
        self._rm().update(
            entry_id,
            SkillHubEntry(
                owner=current.owner,
                name=name,
                description=description,
                source_item=source_item,
                source_app=source_app,
                source_profile=source_profile,
                origin=origin,
                review=review,
                # Set once, when the fork is born. A re-publish does not know
                # (or pass) where the fork came from; the row does.
                forked_from=current.forked_from,
                referenced_tools=list(referenced_tools),
                permission=current.permission,
                blobs=ws,
            ),
        )
        # The row now points at the new version; the old one can go.
        await self._blobs.purge(previous)
        return entry_id

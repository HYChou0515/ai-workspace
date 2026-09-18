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

import contextlib
import uuid
from typing import TYPE_CHECKING, Literal

from msgspec import Struct, field
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError, ResourceIsDeletedError

from ..perm import Permission
from .skill_payload import SkillOrigin, origin_for

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..filestore.protocol import FileStore

#: The FileStore namespace an entry's files live under. A synthetic workspace id
#: per entry, so `purge` on the entry takes exactly its files and nothing else.
_BLOB_PREFIX = "skill-hub:"


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
    #: `skill_update_available` compares the two; two hash implementations kept
    #: alike by hand would diverge the moment one was edited.
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

    def find(self, owner: str, name: str) -> str | None:
        """The entry id for an identity, or ``None``. Scoped by both indexed
        fields, so this is a point lookup rather than a scan."""
        query = ((QB["owner"] == owner) & (QB["name"] == name)).build()
        for res in self._rm().list_resources(query, returns=["info"]):
            return res.info.resource_id
        return None

    async def payload_of(self, entry_id: str) -> dict[str, bytes]:
        """Every file the entry ships, keyed like :func:`skill_payload` keys
        them — the shape ``materialize_skill`` writes into a workspace."""
        ws = _BLOB_PREFIX + entry_id
        out: dict[str, bytes] = {}
        for path in await self._blobs.ls(ws):
            out[path.lstrip("/")] = await self._blobs.read(ws, path)
        return out

    # ── write ────────────────────────────────────────────────────────────

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

        Files are written BEFORE the row, and the row carries their manifest,
        so a row that exists always describes files that exist. A crash between
        the two leaves orphaned blobs, never a row pointing at nothing.
        """
        existing = self.find(owner, name)
        # A NEW entry's id is minted here, not by `create`, so the files can be
        # written under it before the row exists — the order the docstring
        # promises. Asking `create` for the id first would put the row before
        # the files, and a crash in between would leave a row pointing at
        # nothing: the one shape this ordering rules out.
        entry_id = existing if existing is not None else uuid.uuid4().hex

        ws = _BLOB_PREFIX + entry_id
        # Replace, not merge: a file the new version dropped must not survive
        # from the old one, or an installed copy of the new version would carry
        # a reference the SKILL.md no longer makes.
        await self._blobs.purge(ws)
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
                ),
                resource_id=entry_id,
            )
            return entry_id

        current = self.get(entry_id)
        assert current is not None
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
            ),
        )
        return entry_id

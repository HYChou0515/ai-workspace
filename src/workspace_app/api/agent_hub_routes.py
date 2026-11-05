"""Agent Hub (`docs/plan-wui-publish-and-agent-hub.md` A1/A7/A8/A9).

Its own place beside WUI: every published agent the viewer may use (C9), with
usage numbers and no names (A7), and an agent's page — its latest release
notes and the viewer's OWN conversations with it, each a copy (A2), with its
size so a person can see where their space went and delete what they are done
with (C8). Starting a new conversation is the ordinary open
(`POST /published/open`); deleting one is the ordinary item delete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from ..apps.registry import app_model
from ..resources import Conversation
from .agent_routes import NOTES_SHOWN, NoteOut
from .chats import find_default_conversation
from .publish import Publications, Published, canonical_id, may_use, published_id
from .publish_copy import copies_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from specstar import SpecStar

    from ..quota.disk_ledger import DiskLedger
    from .locator import ItemLocator

#: How much of a conversation's first message names it in the list.
HINT_CHARS = 80


class AgentCard(BaseModel):
    id: str
    slug: str
    item_id: str
    path: str
    title: str
    description: str
    icon: str
    color: str
    published_by: str
    published_at: int
    opens: int
    #: Distinct people — never who (A7).
    people: int
    #: Whether this viewer may take it down (`change_permission` on the source).
    can_remove: bool


class AgentList(BaseModel):
    agents: list[AgentCard]


class MyConversation(BaseModel):
    slug: str
    item_id: str
    #: The first thing the viewer asked, cut — "" before they asked anything.
    hint: str
    created_at: int
    #: What it holds on the viewer's quota (C8) — 0 until first measured.
    bytes: int


class AgentPage(BaseModel):
    agent: AgentCard
    notes: list[NoteOut]
    mine: list[MyConversation]


def _hint(conv: Conversation | None) -> str:
    if conv is None:
        return ""
    for m in conv.messages:
        if m.role == "user" and m.content.strip():
            text = " ".join(m.content.split())
            return text if len(text) <= HINT_CHARS else text[: HINT_CHARS - 1] + "…"
    return ""


def register_agent_hub_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    publications: Publications,
    ledger: DiskLedger | None,
    get_user_id: Callable[[], str],
    superusers: frozenset[str] = frozenset(),
) -> None:
    def _may_manage(row: Published) -> bool:
        try:
            locator.require_access(row.slug, row.item_id, "change_permission")
        except HTTPException:
            return False
        return True

    def _card(row: Published) -> AgentCard:
        return AgentCard(
            id=published_id(row.item_id, row.path),
            slug=row.slug,
            item_id=row.item_id,
            path=row.path,
            title=row.title,
            description=row.description,
            icon=row.icon,
            color=row.color,
            published_by=row.published_by,
            published_at=row.published_at,
            opens=row.opens,
            people=len(row.openers),
            can_remove=_may_manage(row),
        )

    @app.get("/agents", response_model=AgentList)
    async def list_agents() -> AgentList:
        user = get_user_id()
        return AgentList(
            agents=[
                _card(row)
                for row in publications.newest_first(kind="agent")
                if may_use(spec, row, user, superusers)
            ]
        )

    @app.get("/agents/{agent_id}", response_model=AgentPage)
    async def agent_page(agent_id: str) -> AgentPage:
        user = get_user_id()
        row = publications.by_id(canonical_id(agent_id))
        # Not usable and not there answer alike: whether an agent exists is
        # not news to someone it is not published to.
        if row is None or row.kind != "agent" or not may_use(spec, row, user, superusers):
            raise HTTPException(status_code=404, detail="no such agent")
        sizes = dict(await ledger.per_item_for(user)) if ledger is not None else {}
        conv_rm = spec.get_resource_manager(Conversation)
        item_rm = spec.get_resource_manager(app_model(row.slug))
        mine: list[MyConversation] = []
        # The access scope applies to `copies_of`, so this is what the viewer
        # may list — and a shared copy someone else made is theirs, not the
        # viewer's, so only the viewer's own are their conversations.
        for item_id, created_by in copies_of(spec, row):
            if created_by != user:
                continue
            default = find_default_conversation(conv_rm, item_id)
            created = item_rm.get_meta(item_id).created_time
            mine.append(
                MyConversation(
                    slug=row.slug,
                    item_id=item_id,
                    hint=_hint(default[1] if default else None),
                    created_at=int(created.timestamp() * 1000),
                    bytes=sizes.get(item_id, 0),
                )
            )
        mine.sort(key=lambda c: -c.created_at)
        return AgentPage(
            agent=_card(row),
            notes=[
                NoteOut(at=n.at, by=n.by, text=n.text) for n in reversed(row.notes[-NOTES_SHOWN:])
            ],
            mine=mine,
        )

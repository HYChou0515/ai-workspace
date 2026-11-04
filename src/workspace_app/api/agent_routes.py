"""The agent panel's summary (`docs/plan-wui-publish-and-agent-hub.md` A6).

Opening an ``agent.ai.yaml`` in the workspace shows what others will get
before it is published (NN/g, *Visibility of system status*): its name and
look, the tools and skills it may use, what its file gets wrong, and whether —
and how — it is published. Every answer is asked of the code a conversation
runs through, never re-derived: the tools of `TurnContextBuilder.agent_tools_of`
(the turn's own two steps), the skills of the index a test chat or a copy
advertises, the problems of the parser that publishing refuses on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel

from ..apps.agent_spec import agent_folder, parse_agent_spec
from ..apps.resolve import resolve_item_agent_config
from ..apps.skills import effective_item_skills, workspace_skill_metas
from ..filestore.protocol import FileNotFound
from .publish import Publications, PublishedOut, ReleaseNote, published_out

if TYPE_CHECKING:
    from specstar import SpecStar

    from ..apps.catalog import AppCatalog
    from ..files import WorkspaceFiles
    from .locator import ItemLocator
    from .turn_context import TurnContextBuilder

#: Release notes the panel shows (A9: "the latest few").
NOTES_SHOWN = 5


class SuggestionOut(BaseModel):
    label: str
    prompt: str


class NoteOut(BaseModel):
    at: int
    by: str
    text: str


class AgentSummary(BaseModel):
    path: str
    #: False when the file does not define an agent yet; ``problems`` says why.
    valid: bool
    problems: list[str]
    title: str = ""
    description: str = ""
    icon: str = ""
    color: str = ""
    instructions: str = ""
    #: Whether ``tools:`` narrows the App's tools at all.
    narrows: bool = False
    #: What a conversation with it holds, in this item.
    tools: list[str] = []
    #: Names its ``tools:`` lists that the App does not grant — not there.
    not_available: list[str] = []
    skills: list[str] = []
    suggestions: list[SuggestionOut] = []
    published: PublishedOut | None = None
    opens: int = 0
    people: int = 0
    notes: list[NoteOut] = []


def _notes(notes: list[ReleaseNote]) -> list[NoteOut]:
    return [NoteOut(at=n.at, by=n.by, text=n.text) for n in reversed(notes[-NOTES_SHOWN:])]


def register_agent_routes(
    app: FastAPI | APIRouter,
    *,
    spec: SpecStar,
    locator: ItemLocator,
    files: WorkspaceFiles,
    publications: Publications,
    turn_ctx: TurnContextBuilder,
    app_catalog: AppCatalog,
) -> None:
    @app.get("/a/{slug}/items/{item_id}/agent", response_model=AgentSummary)
    async def agent_summary(slug: str, item_id: str, path: str = Query(...)) -> AgentSummary:
        workspace_id = locator.require_access(slug, item_id, "read_content")
        path = path if path.startswith("/") else f"/{path}"
        try:
            raw = await files.read(workspace_id, path)
        except FileNotFound:
            raise HTTPException(status_code=404, detail=f"no file at {path}") from None
        row = publications.get(workspace_id, path)
        if row is not None and row.kind != "agent":
            row = None
        state = AgentSummary(
            path=path,
            valid=False,
            problems=[],
            published=published_out(row) if row is not None else None,
            opens=row.opens if row is not None else 0,
            people=len(row.openers) if row is not None else 0,
            notes=_notes(row.notes) if row is not None else [],
        )
        agent, problems = parse_agent_spec(raw.decode("utf-8", errors="replace"))
        if agent is None:
            return state.model_copy(update={"problems": problems})
        cfg = resolve_item_agent_config(spec, app_catalog, workspace_id)
        held, missing = await turn_ctx.agent_tools_of(workspace_id, cfg, path)
        facts = locator.turn_facts(workspace_id)
        # A copy already holds its folder's skills at its root (A5); the
        # author's workspace is asked with the folder, as a test chat is.
        folder = "" if facts.agent_path else agent_folder(path)
        metas = await workspace_skill_metas(files, workspace_id, folder)
        skills = (
            [
                s.name
                for s in effective_item_skills(
                    facts.slug, facts.profile, facts.skill_prefs, metas, tools=held
                )
                if s.effective
            ]
            if facts.slug is not None
            else [m.name for m in metas]
        )
        return state.model_copy(
            update=dict(
                valid=True,
                problems=problems,
                title=agent.title,
                description=agent.description,
                icon=agent.icon,
                color=agent.color,
                instructions=agent.instructions,
                narrows=agent.tools is not None,
                tools=held,
                not_available=missing,
                skills=skills,
                suggestions=[SuggestionOut(label=lab, prompt=p) for lab, p in agent.suggestions],
            )
        )

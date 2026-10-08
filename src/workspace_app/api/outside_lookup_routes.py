"""Answering the "請幫我查" card (docs/plan-outside-lookup.md D4, D7).

The AI asked the person to look something up outside (`ask_outside`); this is
what they bring back. ONE request does both halves — save it under
`lookups/`, then send the message that answers the card (`Message.answers`) —
so the thread never mentions a file that is not there and the workspace never
holds one no message mentions: a send that is refused takes its files back.

Sending asks `converse`. Saving asks `add_content`, as every other way into the
workspace's files does (#847's markings): someone who may chat but not add
files still answers, with the text in the message and no file — and cannot
attach, since an attachment is nothing but a file.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..agent.outside_lookup import TOOL_NAME, declared_lookup
from ..files import WorkspaceFiles, abs_path
from ..filestore.protocol import FileExists
from ..resources.conversation import Conversation
from .chat_routes import SendInto
from .events import FileChanged
from .locator import ItemLocator
from .schemas import _MessageBody
from .turns import ChatTurnEngine

logger = logging.getLogger(__name__)

#: Where the answers are saved — one Markdown file per lookup, attachments in
#: the folder beside it with the same name.
LOOKUPS_DIR = "lookups"

#: How much of the pasted text rides in the message itself. The whole of it is
#: in the file, which the message names; a page pasted whole would otherwise
#: take the next turn's context with it — worst on the small local models an
#: air-gapped deploy runs.
MESSAGE_EXCERPT_CHARS = 4_000

#: Longest summary in a file name, in characters.
_STEM_CHARS = 40


class OutsideAnswerOut(BaseModel):
    """What was saved: the file (None when nothing was — "not found", or a
    person who may not add files) and its attachments."""

    path: str | None
    attachments: list[str]


def register_outside_lookup_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    files: WorkspaceFiles,
    send_into: SendInto,
    turn_engine: ChatTurnEngine,
    get_user_id: Callable[[], str],
    max_file_size: int,
) -> None:
    async def answer(
        request: Request,
        slug: str,
        item_id: str,
        conversation: Callable[[], tuple[str, Conversation]],
        engine_key: Callable[[str, str], str],
        tool_call_id: str,
        kind: str,
        content: str,
        source_url: str,
        reason: str,
        target: str,
        attachments: list[UploadFile],
    ) -> OutsideAnswerOut:
        investigation_id = locator.require_access(slug, item_id, "converse")
        _rid, conv = conversation()
        card = _open_card(conv, tool_call_id)
        subject = card.get("query") or card.get("url") or ""
        saved: list[str] = []
        attached: list[str] = []
        if kind == "not_found":
            text = f"沒查到:{subject}" + (f"\n原因:{reason.strip()}" if reason.strip() else "")
        else:
            body = content.strip()
            if not body and not attachments:
                raise HTTPException(status_code=422, detail="nothing to send — paste or attach")
            may_add = _may(locator, slug, investigation_id)
            if attachments and not may_add:
                raise HTTPException(
                    status_code=403,
                    detail="you may not add files in this workspace — send the text without "
                    "attachments",
                )
            blobs = [
                (_safe_name(f.filename), await _read_capped(f, max_file_size)) for f in attachments
            ]
            if may_add:
                doc = _document(card, body, source_url.strip(), target.strip(), get_user_id())
                saved, attached = await _save(files, investigation_id, subject, doc, blobs)
            text = _message(subject, body, saved[0] if saved else None, attached)
        rid, conv = conversation()  # the writes can wait on a cold sandbox: read it fresh
        try:
            await send_into(
                investigation_id,
                rid,
                conv,
                engine_key(investigation_id, rid),
                _MessageBody(content=text, answers=tool_call_id),
                lane="interactive",
                request=request,
            )
        except BaseException:
            await _take_back(files, investigation_id, [*attached, *saved])
            raise
        who = get_user_id()
        for path in [*saved, *attached]:
            turn_engine.publish(
                investigation_id, FileChanged(path=abs_path(path), by=who, kind="written")
            )
        return OutsideAnswerOut(path=saved[0] if saved else None, attachments=attached)

    @app.post("/a/{slug}/items/{item_id}/outside-answers", response_model=OutsideAnswerOut)
    async def answer_default_chat(
        request: Request,
        slug: str,
        item_id: str,
        tool_call_id: Annotated[str, Form(min_length=1)],
        kind: Annotated[Literal["found", "not_found"], Form()],
        content: Annotated[str, Form()] = "",
        source_url: Annotated[str, Form()] = "",
        reason: Annotated[str, Form()] = "",
        target: Annotated[str, Form()] = "",
        attachments: Annotated[list[UploadFile], File()] = [],  # noqa: B006 — FastAPI's form default
    ) -> OutsideAnswerOut:
        return await answer(
            request,
            slug,
            item_id,
            lambda: locator.conversation_for(item_id),
            # The default chat keys on the ITEM id, like its send route.
            lambda iid, _rid: iid,
            tool_call_id,
            kind,
            content,
            source_url,
            reason,
            target,
            attachments,
        )

    @app.post(
        "/a/{slug}/items/{item_id}/chats/{chat_id}/outside-answers",
        response_model=OutsideAnswerOut,
    )
    async def answer_chat(
        request: Request,
        slug: str,
        item_id: str,
        chat_id: str,
        tool_call_id: Annotated[str, Form(min_length=1)],
        kind: Annotated[Literal["found", "not_found"], Form()],
        content: Annotated[str, Form()] = "",
        source_url: Annotated[str, Form()] = "",
        reason: Annotated[str, Form()] = "",
        target: Annotated[str, Form()] = "",
        attachments: Annotated[list[UploadFile], File()] = [],  # noqa: B006
    ) -> OutsideAnswerOut:
        return await answer(
            request,
            slug,
            item_id,
            lambda: locator.require_chat(slug, item_id, chat_id),
            locator.engine_key,
            tool_call_id,
            kind,
            content,
            source_url,
            reason,
            target,
            attachments,
        )


def _open_card(conv: Conversation, call_id: str) -> dict:
    """The card `call_id` drew in this chat, if it is still open: a call to
    `ask_outside` that declared a card (a refused call drew none) and that no
    message answers yet — a second answer would contradict the first."""
    card = next(
        (
            declared_lookup(m.content)
            for m in conv.messages
            if m.role == "tool" and m.tool_call_id == call_id and m.tool_name == TOOL_NAME
        ),
        None,
    )
    if card is None:
        raise HTTPException(status_code=404, detail="no lookup card with this id in this chat")
    if any(m.answers == call_id for m in conv.messages):
        raise HTTPException(status_code=409, detail="this lookup card is already answered")
    return card


def _may(locator: ItemLocator, slug: str, item_id: str) -> bool:
    try:
        locator.require_access(slug, item_id, "add_content")
    except HTTPException:
        return False
    return True


async def _read_capped(upload: UploadFile, cap: int) -> bytes:
    data = await upload.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(status_code=413, detail=f"{upload.filename} is larger than {cap} bytes")
    return data


def _safe_name(name: str | None) -> str:
    """The attachment's own name, without any directory it claims — a name
    is not a path into the workspace."""
    base = PurePosixPath((name or "").replace("\\", "/")).name.strip()
    return base if base not in ("", ".", "..") else "attachment"


def _stem(subject: str) -> str:
    """A file-name-safe summary of the query or URL: letters and digits of any
    script, the rest folded into single dashes."""
    words = re.sub(r"^https?://", "", subject)
    slug = re.sub(r"[^\w]+", "-", words).strip("-_")[:_STEM_CHARS].strip("-_")
    return slug or "lookup"


def _document(card: dict, body: str, source_url: str, target: str, who: str) -> str:
    subject = card.get("query") or card.get("url") or ""
    lines = [f"# 外部查詢:{subject}", "", f"- 為什麼:{card['why']}"]
    lines.append(f"- 查詢:{card['query']}" if "query" in card else f"- 網址:{card['url']}")
    if target:
        lines.append(f"- 用:{target}")
    if source_url:
        lines.append(f"- 來源:{source_url}")
    lines += [f"- 查的人:{who}", f"- 時間:{datetime.now(UTC).isoformat(timespec='seconds')}"]
    return "\n".join([*lines, "", "---", "", body, ""])


async def _save(
    files: WorkspaceFiles,
    workspace_id: str,
    subject: str,
    doc: str,
    blobs: list[tuple[str, bytes]],
) -> tuple[list[str], list[str]]:
    """Write the document and its attachments; the paths written. Room is
    checked for all of it up front, so a full workspace refuses the whole
    answer rather than half of it (#538)."""
    data = doc.encode()
    await files.ensure_room_for(workspace_id, len(data) + sum(len(b) for _n, b in blobs))
    base = f"{LOOKUPS_DIR}/{datetime.now(UTC):%Y-%m-%d}-{_stem(subject)}"
    stem = await _claim(files, workspace_id, base, data)
    attached: list[str] = []
    try:
        taken: set[str] = set()
        for name, blob in blobs:
            name = _unique(name, taken)
            path = f"{stem}/{name}"
            await files.create_exclusive(workspace_id, path, blob)
            attached.append(path)
    except BaseException:
        await _take_back(files, workspace_id, [*attached, f"{stem}.md"])
        raise
    return [f"{stem}.md"], attached


async def _claim(files: WorkspaceFiles, workspace_id: str, base: str, data: bytes) -> str:
    """The first free `base`, `base-2`, … — its `.md` created with `data`.
    Bounded: past the bound something other than a busy day is wrong."""
    for n in range(1, 100):
        stem = base if n == 1 else f"{base}-{n}"
        try:
            await files.create_exclusive(workspace_id, f"{stem}.md", data)
        except FileExists:
            continue
        return stem
    raise HTTPException(status_code=409, detail=f"too many lookups named {base}")


def _unique(name: str, taken: set[str]) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 1
    while candidate in taken:
        n += 1
        candidate = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
    taken.add(candidate)
    return candidate


async def _take_back(files: WorkspaceFiles, workspace_id: str, paths: list[str]) -> None:
    for path in paths:
        try:
            await files.delete(workspace_id, path)
        except Exception:  # noqa: BLE001 — the answer's own failure is what the caller reports
            logger.warning("outside lookup: could not take back %s", path, exc_info=True)


def _message(subject: str, body: str, path: str | None, attached: list[str]) -> str:
    """What the AI reads next turn: what was looked up, where it is saved, and
    the text — excerpted when the file holds the rest."""
    lines = [f"我在外面查了:{subject}"]
    if path:
        lines.append(f"存在 `{path}`")
    if attached:
        lines.append("附件:" + "、".join(f"`{p}`" for p in attached))
    if body:
        excerpt = body
        if path and len(body) > MESSAGE_EXCERPT_CHARS:
            excerpt = body[:MESSAGE_EXCERPT_CHARS] + f"\n\n…(以下省略,完整內容在 `{path}`)"
        lines += ["", excerpt]
    return "\n".join(lines)


__all__ = ["OutsideAnswerOut", "register_outside_lookup_routes"]

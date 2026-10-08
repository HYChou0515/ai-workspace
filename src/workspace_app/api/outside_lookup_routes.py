"""Answering the "請幫我查" card (docs/plan-outside-lookup.md D4, D7).

The AI asked the person to look something up outside (`ask_outside`); this is
what they bring back. ONE request does both halves — save it under
`lookups/`, then send the message that answers the card (`Message.answers`) —
so the thread does not mention a file that is not there, and a send refused
before its message was persisted takes its files back.

One answer per card. Answers to the same card on THIS pod run one at a time
(`_card_locks`), and each re-reads the thread after its writes, so the second
finds the card answered and takes its files back with a 409. Two pods
answering the same card in the same instant can still both get through —
the window is the length of one send, and closing it would need a
cross-pod lock for an event two people have to race to produce.

Sending asks `converse`. Saving asks `add_content`, as every other way into the
workspace's files does (#847's markings): someone who may chat but not add
files still answers, with the text in the message and no file — and cannot
attach, since an attachment is nothing but a file.
"""

from __future__ import annotations

import asyncio
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

#: The person's own date, as their browser sends it (`YYYY-MM-DD`).
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: One lock per (item, card) being answered on this pod — see the module doc.
#: Entries go when their last holder leaves, so the map holds only answers in
#: flight.
_card_locks: dict[tuple[str, str], asyncio.Lock] = {}


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
        form: _Form,
    ) -> OutsideAnswerOut:
        investigation_id = locator.require_access(slug, item_id, "converse")
        key = (investigation_id, form.tool_call_id)
        lock = _card_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                return await _answer_once(
                    request, slug, investigation_id, conversation, engine_key, form
                )
        finally:
            if not lock.locked() and _card_locks.get(key) is lock:
                del _card_locks[key]

    async def _answer_once(
        request: Request,
        slug: str,
        investigation_id: str,
        conversation: Callable[[], tuple[str, Conversation]],
        engine_key: Callable[[str, str], str],
        form: _Form,
    ) -> OutsideAnswerOut:
        _rid, conv = conversation()
        card = _open_card(conv, form.tool_call_id)
        asked = card.get("query") or card.get("url") or ""
        # What was actually searched: the query as the person edited it (D8).
        searched = (form.query.strip() if "query" in card else "") or asked
        written: list[str] = []
        saved: str | None = None
        attached: list[str] = []
        if form.kind == "not_found":
            text = f"沒有查到／不查了:{searched}" + (
                f"\n原因:{form.reason.strip()}" if form.reason.strip() else ""
            )
        else:
            body = (await _read_text(form.content, max_file_size)).strip()
            if not body and not form.attachments:
                raise HTTPException(status_code=422, detail="nothing to send — paste or attach")
            may_add = _may(locator, slug, investigation_id)
            if form.attachments and not may_add:
                raise HTTPException(
                    status_code=403,
                    detail="you may not add files in this workspace — send the text without "
                    "attachments",
                )
            blobs = [
                (_safe_name(f.filename), await _read_capped(f, max_file_size))
                for f in form.attachments
            ]
            if may_add:
                doc = _document(card, searched, body, form, get_user_id())
                day = form.date if _is_date(form.date) else f"{datetime.now(UTC):%Y-%m-%d}"
                saved, attached = await _save(files, investigation_id, day, searched, doc, blobs)
                written = [*attached, saved]
            text = _message(searched, body, saved, attached)
        try:
            rid, conv = conversation()  # the writes can wait on a cold sandbox: read it fresh
            # Answered meanwhile — another tab, another viewer: theirs stands.
            _open_card(conv, form.tool_call_id)
            await send_into(
                investigation_id,
                rid,
                conv,
                engine_key(investigation_id, rid),
                _MessageBody(content=text, answers=form.tool_call_id),
                lane="interactive",
                request=request,
            )
        except Exception:
            # Taken back only when no message names them. The send is shielded
            # (`ChatSendService.send`): a failure after it persisted the
            # message leaves a message pointing at these files.
            if not _answered_by(conversation, form.tool_call_id):
                await _take_back(files, investigation_id, written)
            raise
        # A cancelled request (`CancelledError` is not an `Exception`) keeps its
        # files: the shielded send carries on and persists the message.
        who = get_user_id()
        for path in written:
            turn_engine.publish(
                investigation_id, FileChanged(path=abs_path(path), by=who, kind="written")
            )
        return OutsideAnswerOut(path=saved, attachments=attached)

    @app.post("/a/{slug}/items/{item_id}/outside-answers", response_model=OutsideAnswerOut)
    async def answer_default_chat(
        request: Request,
        slug: str,
        item_id: str,
        tool_call_id: Annotated[str, Form(min_length=1)],
        kind: Annotated[Literal["found", "not_found"], Form()],
        content: Annotated[UploadFile | None, File()] = None,
        query: Annotated[str, Form()] = "",
        source_url: Annotated[str, Form()] = "",
        reason: Annotated[str, Form()] = "",
        target: Annotated[str, Form()] = "",
        date: Annotated[str, Form()] = "",
        attachments: Annotated[list[UploadFile], File()] = [],  # noqa: B006 — FastAPI's form default
    ) -> OutsideAnswerOut:
        return await answer(
            request,
            slug,
            item_id,
            lambda: locator.conversation_for(item_id),
            # The default chat keys on the ITEM id, like its send route.
            lambda iid, _rid: iid,
            _Form(
                tool_call_id, kind, content, query, source_url, reason, target, date, attachments
            ),
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
        content: Annotated[UploadFile | None, File()] = None,
        query: Annotated[str, Form()] = "",
        source_url: Annotated[str, Form()] = "",
        reason: Annotated[str, Form()] = "",
        target: Annotated[str, Form()] = "",
        date: Annotated[str, Form()] = "",
        attachments: Annotated[list[UploadFile], File()] = [],  # noqa: B006
    ) -> OutsideAnswerOut:
        return await answer(
            request,
            slug,
            item_id,
            lambda: locator.require_chat(slug, item_id, chat_id),
            locator.engine_key,
            _Form(
                tool_call_id, kind, content, query, source_url, reason, target, date, attachments
            ),
        )


class _Form:
    """The answer as posted. `content` — the pasted text — is a FILE part, not
    a field: Starlette caps a plain field at 1 MiB, and one pasted page in CJK
    (three bytes a character) passes that."""

    def __init__(
        self,
        tool_call_id: str,
        kind: str,
        content: UploadFile | None,
        query: str,
        source_url: str,
        reason: str,
        target: str,
        date: str,
        attachments: list[UploadFile],
    ) -> None:
        self.tool_call_id = tool_call_id
        self.kind = kind
        self.content = content
        self.query = query
        self.source_url = source_url.strip()
        self.reason = reason
        self.target = target.strip()
        self.date = date.strip()
        self.attachments = attachments


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


def _answered_by(conversation: Callable[[], tuple[str, Conversation]], call_id: str) -> bool:
    """Whether the thread now holds a message answering `call_id` — read fresh;
    a thread that cannot be read is taken as not answered."""
    try:
        _rid, conv = conversation()
    except Exception:  # noqa: BLE001 — the caller is already failing; this only decides cleanup
        return False
    return any(m.answers == call_id for m in conv.messages)


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


async def _read_text(upload: UploadFile | None, cap: int) -> str:
    if upload is None:
        return ""
    data = await _read_capped(upload, cap)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="the pasted text is not UTF-8") from exc


def _is_date(day: str) -> bool:
    if not _DATE.fullmatch(day):
        return False
    try:
        datetime.strptime(day, "%Y-%m-%d")  # noqa: DTZ007 — a calendar date, no time to zone
    except ValueError:
        return False
    return True


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


def _document(card: dict, searched: str, body: str, form: _Form, who: str) -> str:
    asked = card.get("query") or card.get("url") or ""
    lines = [f"# 外部查詢:{searched}", "", f"- 為什麼:{card['why']}"]
    if "query" in card:
        lines.append(f"- AI 的查詢:{asked}")
        if searched != asked:
            lines.append(f"- 實際搜尋:{searched}")
    else:
        lines.append(f"- 網址:{asked}")
    if form.target:
        lines.append(f"- 用:{form.target}")
    if form.source_url:
        lines.append(f"- 來源:{form.source_url}")
    lines += [f"- 查的人:{who}", f"- 時間:{datetime.now(UTC).isoformat(timespec='seconds')}"]
    return "\n".join([*lines, "", "---", "", body, ""])


async def _save(
    files: WorkspaceFiles,
    workspace_id: str,
    day: str,
    subject: str,
    doc: str,
    blobs: list[tuple[str, bytes]],
) -> tuple[str, list[str]]:
    """Write the document and its attachments; the paths written. Room is
    checked for all of it up front, so a full workspace refuses the whole
    answer rather than half of it (#538)."""
    data = doc.encode()
    await files.ensure_room_for(workspace_id, len(data) + sum(len(b) for _n, b in blobs))
    stem = await _claim(files, workspace_id, f"{LOOKUPS_DIR}/{day}-{_stem(subject)}", data)
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
    return f"{stem}.md", attached


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

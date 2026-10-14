"""The helper's jobs, shared across pods (docs/plan-outside-helper.md §3.2).

A job is one errand the card hands to the person's own helper — a Python
program on their PC that drives Chrome, since only the browser reaches the
internet there. It lives in the shared backend (specstar), so the pod that took
the card's request and the pod that answers the helper's poll need not be the
same one. Registered after ``spec.apply``, like ``_SandboxAddress``: no CRUD
routes are generated for it, only the routes in ``outside_job_routes``.

A job belongs to the person who handed it over (``owner``) and only that
person's helper takes it. States: ``queued`` → ``claimed`` → ``done`` |
``failed`` | ``cancelled``; a claim that is never finished goes back to
``queued`` after ``CLAIM_TIMEOUT_MS`` (the helper was closed or crashed).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
from collections.abc import Callable
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from msgspec import Struct
from specstar import QB, SpecStar
from specstar.types import (
    DuplicateResourceError,
    PreconditionFailedError,
    ResourceIDNotFoundError,
    ResourceIsDeletedError,
    RevisionStatus,
)

from ..agent.outside_lookup import LINK_LIST_MARKER, is_web_address

JobKind = Literal["search", "open_url"]
JobState = Literal["queued", "claimed", "done", "failed", "cancelled"]

_MAX_CAS_RETRIES = 100

#: A claimed job with no result after this long goes back to the queue.
CLAIM_TIMEOUT_MS = 120_000

#: A helper that has not checked in for this long is offline: the card offers
#: the manual flow (D7) rather than a 「查」 nobody will act on.
ONLINE_WINDOW_MS = 30_000

#: A job taken this many times and never finished — the workspace full, a
#: refusal that persists, a helper crashing on that page — is not taken again
#: but ends, with why: otherwise it is fetched every CLAIM_TIMEOUT_MS forever.
MAX_CLAIMS = 3

#: The oldest helper↔backend protocol this backend speaks (§3.7.3). Raise it
#: when an API change would break helpers already installed: they then get a
#: 426 and update, instead of quietly misreading the new API.
MIN_PROTOCOL = 1


class OutsideJob(Struct):
    owner: str
    item_id: str
    slug: str
    chat_id: str
    tool_call_id: str
    kind: JobKind
    args: str
    state: JobState
    created_ms: int
    claimed_ms: int = 0
    error: str = ""
    #: What the helper is waiting on while it holds the job (`captcha`: the
    #: person solves one in the helper's Chrome window, §3.4); "" otherwise.
    note: str = ""
    #: Handed over by the card on its own — a link an earlier lookup in this
    #: chat brought back (D12) — rather than with the person's 「查」.
    auto: bool = False
    #: Where the page was saved once the job is done (`lookups/…`).
    saved: str = ""
    #: How many times a helper has taken it (see MAX_CLAIMS).
    claims: int = 0


class _AiWorkspaceHelperPresence(Struct):
    """The last time a person's helper checked in, and what it runs."""

    owner: str
    last_seen_ms: int
    version: str = ""
    protocol: int = 0
    #: What it drives — who is about to break when Chrome updates past what
    #: the helper's Playwright was tested with (§3.7.7).
    chrome: str = ""
    playwright: str = ""
    python: str = ""
    #: Sent only by 0.1.0 (D15's automatic update); kept — the API only adds
    #: (docs/plan-outside-helper.md D21, §3.7.3).
    update: str = ""


class _OutsideSeenUrls(Struct):
    """The links lookups in one chat brought back (D12): opening one of these
    sends nothing the person has not already seen come in from outside."""

    item_id: str
    chat_id: str
    urls: list[str]


#: Most links remembered per chat; the oldest go first.
SEEN_URLS_CAP = 2_000

#: Fenced code and code spans: what looks like a link in there is code.
_FENCE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
_CODE_SPAN = re.compile(r"(`+)(?!`).+?(?<!`)\1", re.DOTALL)
#: `<https://…>` — an autolink.
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")


def _blanked(m: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def _destination(text: str, at: int) -> str | None:
    """The address of an inline link whose `(` is just before `at`: `<…>`, or
    up to whitespace (a title follows) or the `)` that closes it — parentheses
    in it balanced, `\(` `\)` unescaped (the paste converter escapes them:
    Wikipedia's `Foo_(bar)`). `None` when there is none."""
    if text.startswith("<", at):
        end = text.find(">", at)
        return text[at + 1 : end] if end > 0 and "\n" not in text[at:end] else None
    out: list[str] = []
    depth = 0
    while at < len(text):
        c = text[at]
        if c == "\\" and at + 1 < len(text) and text[at + 1] in "()\\":
            out.append(text[at + 1])
            at += 2
            continue
        if c.isspace() or (c == ")" and depth == 0):
            break
        depth += {"(": 1, ")": -1}.get(c, 0)
        out.append(c)
        at += 1
    return "".join(out) or None


def _opening(text: str, close: int) -> int | None:
    """Where the `[` matching the `]` at `close` is; brackets inside nest."""
    depth = 0
    for at in range(close - 1, -1, -1):
        if at > 0 and text[at - 1] == "\\":
            continue
        if text[at] == "]":
            depth += 1
        elif text[at] == "[":
            if depth == 0:
                return at
            depth -= 1
    return None


def _links(markdown: str) -> list[tuple[int, bool, str, str]]:
    """Every link in `markdown` — `(where, is_image, text, address)` in order —
    read the way Markdown reads one: `[text](address "title")`, `![alt](…)`,
    `<https://…>`; nothing inside code. Read by a scanner, not one pattern: a
    pattern ended `Foo_(bar)` at its first `)` (review of #941)."""
    text = _CODE_SPAN.sub(_blanked, _FENCE.sub(_blanked, markdown))
    found = [(m.start(), False, "", m.group(1)) for m in _AUTOLINK.finditer(text)]
    close = text.find("](")
    while close >= 0:
        address = _destination(text, close + 2)
        start = _opening(text, close)
        if address is not None and start is not None:
            image = start > 0 and text[start - 1] == "!"
            found.append((start, image, markdown[start + 1 : close], address))
        close = text.find("](", close + 2)
    return sorted(found)


#: Google's own hosts: www.google.com, google.com.tw, …
_GOOGLE = re.compile(r"(?:www\.)?google(?:\.[a-z]{2,3}){1,2}")


def _page_of(url: str) -> str:
    """A Google redirect link (`https://www.google.com/url?q=<page>&sa=U…`, what
    a search's results carry) as the page it leads to; any other link as it is
    (D24). Only an http(s) page counts — `?q=javascript:…` stays the redirect."""
    parts = urlsplit(url)
    if parts.path == "/url" and _GOOGLE.fullmatch(parts.hostname or ""):
        params = parse_qs(parts.query)
        for target in (*params.get("q", []), *params.get("url", [])):
            if is_web_address(target):
                return target
    return url


def links_in(markdown: str) -> list[str]:
    """The web links a lookup's Markdown carries (images too), in order, each
    once, without a `#fragment` (the same page whichever anchor it was linked
    at) — a Google redirect both as it came and as the page it leads to (D24):
    what came back stays openable with no nod, and so does the page the list
    names."""
    out: list[str] = []
    for _, _, _, address in _links(markdown):
        for url in (address, _page_of(address)):
            url = _without_fragment(url)
            if is_web_address(url) and url not in out:
                out.append(url)
    return out


def numbered_links(markdown: str) -> list[tuple[int, str, str]]:
    """`(n, text, page)` for each web page the answer links to — not images —
    numbered in order from 1, the same page (whatever its `#anchor`) one
    number, a Google redirect as the page it leads to (D24)."""
    pages: dict[str, str] = {}
    for _, image, text, address in _links(markdown):
        page = _without_fragment(_page_of(address))
        if not image and is_web_address(page) and page not in pages:
            pages[page] = " ".join(text.split())
    return [(n, text, page) for n, (page, text) in enumerate(pages.items(), start=1)]


def link_list(numbered: list[tuple[int, str, str]]) -> str:
    """The list that goes under a saved answer — after `LINK_LIST_MARKER`, one
    `- [n] <page> text` per link — or `""` when there is nothing to list. The
    answer above it is left as it came (review of #941: rewriting its links in
    place broke some and collided with others)."""
    if not numbered:
        return ""
    rows = "\n".join(f"- [{n}] <{page}> {text}".rstrip() for n, text, page in numbered)
    return f"\n\n{LINK_LIST_MARKER}\n## 連結\n\n{rows}\n"


def _without_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def job_id(item_id: str, chat_id: str, tool_call_id: str) -> str:
    """One job per card: handing it over again replaces the previous one.
    Hashed — an item id may hold characters a specstar id may not."""
    return hashlib.sha1(f"{item_id}\0{chat_id}\0{tool_call_id}".encode()).hexdigest()


def _owner_key(owner: str) -> str:
    return hashlib.sha1(owner.encode()).hexdigest()


def register_outside_jobs(spec: SpecStar) -> None:
    """Idempotent; called after ``spec.apply`` so no CRUD routes exist."""
    with contextlib.suppress(ValueError):
        spec.add_model(OutsideJob, indexed_fields=["owner", "state", "created_ms"])
    with contextlib.suppress(ValueError):
        spec.add_model(_AiWorkspaceHelperPresence)
    with contextlib.suppress(ValueError):
        spec.add_model(_OutsideSeenUrls)


def _ms() -> int:
    return int(time.time() * 1000)


class OutsideJobStore:
    """Jobs and helper presence over specstar. Blocking specstar I/O runs in a
    thread, as everywhere else in the app."""

    def __init__(self, spec: SpecStar, *, now: Callable[[], int] = _ms) -> None:
        self._spec = spec
        self.now = now

    def _jobs(self):
        return self._spec.get_resource_manager(OutsideJob)

    def _presence(self):
        return self._spec.get_resource_manager(_AiWorkspaceHelperPresence)

    # ── presence ────────────────────────────────────────────────────────────

    async def heartbeat(self, owner: str, **seen: str | int) -> None:
        """The owner's helper checked in; ``seen`` is what it runs on — the
        `_AiWorkspaceHelperPresence` fields past ``last_seen_ms``."""
        await asyncio.to_thread(self._heartbeat_sync, owner, seen)

    def _heartbeat_sync(self, owner: str, seen: dict[str, str | int]) -> None:
        row = _AiWorkspaceHelperPresence(
            owner=owner,
            last_seen_ms=self.now(),
            **seen,  # ty: ignore[invalid-argument-type]
        )
        rm = self._presence()
        key = _owner_key(owner)
        try:
            rm.get(key)
        except ResourceIDNotFoundError:
            with contextlib.suppress(DuplicateResourceError):  # a second poll raced us
                rm.create(row, resource_id=key, if_not_exists=True)
                return
        except ResourceIsDeletedError:
            rm.restore(key)
        rm.modify(key, row, status=RevisionStatus.draft)

    async def online(self, owner: str) -> bool:
        return await asyncio.to_thread(self._online_sync, owner)

    def _online_sync(self, owner: str) -> bool:
        seen = self.presence(owner)
        return seen is not None and self.now() - seen.last_seen_ms <= ONLINE_WINDOW_MS

    def presence(self, owner: str) -> _AiWorkspaceHelperPresence | None:
        """The owner's helper as it last checked in, or None (blocking)."""
        try:
            data = self._presence().get(_owner_key(owner)).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(data, _AiWorkspaceHelperPresence)
        return data

    # ── jobs ────────────────────────────────────────────────────────────────

    async def hand_over(self, job: OutsideJob) -> str:
        """Queue ``job`` for its owner's helper, replacing an earlier job for
        the same card. Returns the job id."""
        return await asyncio.to_thread(self._hand_over_sync, job)

    def _hand_over_sync(self, job: OutsideJob) -> str:
        jid = job_id(job.item_id, job.chat_id, job.tool_call_id)
        rm = self._jobs()
        try:
            rm.get(jid)
        except ResourceIDNotFoundError:
            rm.create(job, resource_id=jid)
            return jid
        except ResourceIsDeletedError:
            rm.restore(jid)
        rm.modify(jid, job, status=RevisionStatus.draft)
        return jid

    async def get(self, jid: str) -> OutsideJob | None:
        return await asyncio.to_thread(self._get_sync, jid)

    def _get_sync(self, jid: str) -> OutsideJob | None:
        try:
            data = self._jobs().get(jid).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return None
        assert isinstance(data, OutsideJob)
        return data

    async def claim(self, owner: str) -> tuple[str, OutsideJob] | None:
        """The owner's oldest waiting job, now claimed — or None. A compare-and-
        swap on the row's etag, so two polls (two pods, two helper windows)
        never take the same job."""
        return await asyncio.to_thread(self._claim_sync, owner)

    def _claim_sync(self, owner: str) -> tuple[str, OutsideJob] | None:
        rm = self._jobs()
        now = self.now()
        # Waiting jobs, then claims nobody finished in time (the helper was
        # closed or crashed) — both narrowed by the indexed owner + state, so
        # the age check below only ever sees this person's own handful.
        waiting = ((QB["owner"] == owner) & (QB["state"] == "queued")).sort("created_ms")
        stale = ((QB["owner"] == owner) & (QB["state"] == "claimed")).sort("created_ms")
        for query in (waiting, stale):
            for res in rm.list_resources(query.build()):
                data = res.data
                assert isinstance(data, OutsideJob)
                if data.state == "claimed" and now - data.claimed_ms <= CLAIM_TIMEOUT_MS:
                    continue
                jid = res.info.resource_id
                if data.claims >= MAX_CLAIMS:
                    with contextlib.suppress(PreconditionFailedError):
                        rm.modify(
                            jid,
                            _with(
                                data,
                                state="failed",
                                error=f"the helper took it {MAX_CLAIMS} times and never "
                                "finished — stopped trying",
                            ),
                            status=RevisionStatus.draft,
                            expected_etag=res.info.etag,
                        )
                    continue
                claimed = _with(data, state="claimed", claimed_ms=now, claims=data.claims + 1)
                try:
                    rm.modify(
                        jid,
                        claimed,
                        status=RevisionStatus.draft,
                        expected_etag=res.info.etag,
                    )
                except PreconditionFailedError:
                    continue  # another poll took it first
                return jid, claimed
        return None

    async def fail(self, jid: str, owner: str, error: str) -> bool:
        """Mark the owner's claimed job failed with ``error``. False when it is
        not theirs, not claimed, or gone — the helper gets a 404, not the power
        to end someone else's job."""
        return await asyncio.to_thread(self._finish_sync, jid, owner, "failed", error)

    async def note(self, jid: str, owner: str, note: str) -> bool:
        """Say what the owner's helper is waiting on for this claimed job. It
        also renews the claim: a person solving a captcha can take longer than
        `CLAIM_TIMEOUT_MS`, so the helper says it again every
        `ai_workspace_helper.errands.RENEW_S` while it waits, and the job does not go
        back to the queue — to be fetched a second time — meanwhile. False as
        `fail`."""
        return await asyncio.to_thread(self._note_sync, jid, owner, note)

    def _note_sync(self, jid: str, owner: str, note: str) -> bool:
        rm = self._jobs()
        try:
            res = rm.get(jid)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return False
        data = res.data
        assert isinstance(data, OutsideJob)
        if data.owner != owner or data.state != "claimed":
            return False
        try:
            rm.modify(
                jid,
                _with(data, note=note, claimed_ms=self.now()),
                status=RevisionStatus.draft,
                expected_etag=res.info.etag,
            )
        except PreconditionFailedError:
            return False  # cancelled or answered meanwhile
        # Busy with the person, not gone: this counts as checking in.
        self._touch_sync(owner)
        return True

    def _touch_sync(self, owner: str) -> None:
        """Renew the owner's presence, keeping what its last check-in said."""
        seen = self.presence(owner)
        if seen is None:
            return
        fields = {f: getattr(seen, f) for f in seen.__struct_fields__}
        row = _AiWorkspaceHelperPresence(**{**fields, "last_seen_ms": self.now()})  # ty: ignore[invalid-argument-type]
        self._presence().modify(_owner_key(owner), row, status=RevisionStatus.draft)

    # ── links lookups brought back (D12) ────────────────────────────────────

    async def remember_links(self, item_id: str, chat_id: str, markdown: str) -> None:
        urls = links_in(markdown)
        if urls:
            await asyncio.to_thread(self._remember_sync, item_id, chat_id, urls)

    def _remember_sync(self, item_id: str, chat_id: str, urls: list[str]) -> None:
        rm = self._spec.get_resource_manager(_OutsideSeenUrls)
        key = job_id(item_id, chat_id, "")
        for _ in range(_MAX_CAS_RETRIES):
            try:
                res = rm.get(key)
            except (ResourceIDNotFoundError, ResourceIsDeletedError):
                try:
                    rm.create(
                        _OutsideSeenUrls(
                            item_id=item_id, chat_id=chat_id, urls=urls[-SEEN_URLS_CAP:]
                        ),
                        resource_id=key,
                        if_not_exists=True,  # ty: ignore[unknown-argument]
                    )
                    return
                except DuplicateResourceError:
                    continue  # another answer wrote first: merge into theirs
            data = res.data
            assert isinstance(data, _OutsideSeenUrls)
            merged = [u for u in data.urls if u not in urls] + urls
            try:
                rm.modify(
                    key,
                    _OutsideSeenUrls(
                        item_id=item_id, chat_id=chat_id, urls=merged[-SEEN_URLS_CAP:]
                    ),
                    status=RevisionStatus.draft,
                    expected_etag=res.info.etag,  # ty: ignore[unknown-argument]
                )
                return
            except PreconditionFailedError:
                continue
        raise RuntimeError(f"seen-links CAS exhausted retries for {key!r}")  # pragma: no cover

    async def seen(self, item_id: str, chat_id: str, url: str) -> bool:
        return await asyncio.to_thread(self._seen_sync, item_id, chat_id, url)

    def _seen_sync(self, item_id: str, chat_id: str, url: str) -> bool:
        rm = self._spec.get_resource_manager(_OutsideSeenUrls)
        try:
            data = rm.get(job_id(item_id, chat_id, "")).data
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return False
        assert isinstance(data, _OutsideSeenUrls)
        return _without_fragment(url) in data.urls

    async def end(self, jid: str, owner: str, state: JobState, *, saved: str = "") -> bool:
        """End the owner's claimed job as ``state`` (``done`` once its result
        answered the card, ``cancelled`` when the card was answered meanwhile)."""
        return await asyncio.to_thread(self._finish_sync, jid, owner, state, "", saved)

    async def cancel(self, jid: str) -> bool:
        """Cancel a job that has not finished. False when there is none."""
        return await asyncio.to_thread(self._finish_sync, jid, None, "cancelled", "")

    def _finish_sync(
        self, jid: str, owner: str | None, state: JobState, error: str, saved: str = ""
    ) -> bool:
        rm = self._jobs()
        try:
            res = rm.get(jid)
        except (ResourceIDNotFoundError, ResourceIsDeletedError):
            return False
        data = res.data
        assert isinstance(data, OutsideJob)
        if owner is not None and (data.owner != owner or data.state != "claimed"):
            return False
        if data.state not in ("queued", "claimed"):
            return False
        try:
            rm.modify(
                jid,
                _with(data, state=state, error=error, saved=saved),
                status=RevisionStatus.draft,
                expected_etag=res.info.etag,
            )
        except PreconditionFailedError:
            return False  # it changed under us (claimed, finished): theirs stands
        return True


def _with(job: OutsideJob, **changes: object) -> OutsideJob:
    fields = {f: getattr(job, f) for f in job.__struct_fields__}
    return OutsideJob(**{**fields, **changes})  # ty: ignore[invalid-argument-type]


__all__ = [
    "CLAIM_TIMEOUT_MS",
    "ONLINE_WINDOW_MS",
    "OutsideJob",
    "OutsideJobStore",
    "job_id",
    "register_outside_jobs",
]

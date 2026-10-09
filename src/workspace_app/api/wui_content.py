"""A WUI served from a real URL (`docs/plan-wui-multipage.md`).

A page used to reach its frame as ONE assembled document in `srcdoc`, whose
`location` is `about:srcdoc` — not hierarchical, so every script that derives a
URL from `location` throws on its first line (measured: mkdocs-material's does).
A multi-page site needs each page at an `http(s)` URL, with its siblings beside
it, the way a static file server would hand them out.

The frame is opaque-origin (`sandbox="allow-scripts"`, and the `sandbox` CSP
directive below for anyone opening the URL directly), and an opaque origin's
requests carry no `SameSite=Lax` cookie — scripts, stylesheets, `fetch`, even its
own link navigations all go out `cross-site` (Phase 1 measured each). So the
content route cannot lean on the session the rest of the API uses. It
authorises by a **pass** instead: a random token the pane mints through a
cookie-authenticated route, stored as a specstar row so every pod honours it,
naming the item, the folder and the user it was minted for.

A pass is a capability, not an identity: whoever holds the URL reads that
folder until it expires. What keeps that narrow is that the pass is bound to
ONE folder, read-only, and re-checked against the item's live permission for
its user on every request — a user who loses the item loses the page within the
locator's access window, not at expiry.
"""

from __future__ import annotations

import contextlib
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from msgspec import Struct
from pydantic import BaseModel
from specstar import QB, SpecStar
from specstar.types import ResourceIDNotFoundError

from ..files import WorkspaceFiles
from ..files.media_type import media_type_for
from ..filestore.protocol import FileNotFound
from .file_routes import _workspace_path
from .locator import ItemLocator

#: How long a pass reads its folder. Long, because a reader keeps a docs page
#: open for an afternoon and every link they follow presents the same pass —
#: an expiry mid-reading is a page that stops working for no reason they can
#: see. Not forever, because a pass is a URL and URLs leak.
PASS_TTL_S = 12 * 60 * 60

#: A pass with less than this left is not handed out again: the next mint gets
#: a fresh one. So a pane is handed a pass with at least half its life left — an
#: open page keeps working for at least `PASS_TTL_S - _REUSE_MARGIN_S` (six
#: hours), and Refresh mints again.
_REUSE_MARGIN_S = PASS_TTL_S // 2

#: How many lapsed passes one mint clears. Bounded so a mint never turns into a
#: sweep of the whole table; each mint clears a few, and lapsed rows are only
#: ever made at the rate passes are minted.
_PRUNE_BATCH = 50

#: How long a looked-up pass is held, and how many are, before asking again.
_HOLD_S = 5.0
_HOLD_MAX = 1024

#: The envelope, as the server's own header — a page's `<meta>` can only
#: tighten it. `sandbox` makes the document opaque-origin even when the URL is
#: opened as a top-level page; `'self'` is this host and nothing else, so a page
#: cannot send what it read anywhere; `blob:` workers because an opaque origin
#: may not start a worker from a URL (Phase 1), so the runtime loads the script
#: into a blob. `form-action 'none'`: a form post is a request out.
CONTENT_CSP = "; ".join(
    [
        "sandbox allow-scripts",
        "default-src 'none'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "media-src 'self' data:",
        "connect-src 'self'",
        "worker-src blob:",
        "form-action 'none'",
        "base-uri 'self'",
    ]
)

#: What `__wui/ping` answers when this route can serve a page. The pane probes
#: it without cookies before pointing a frame here; any other answer — a
#: gateway's login page, an older server, a build without the runtime — sends
#: it to the single-page fallback.
PING_ANSWER = "wui-content"

_HEAD = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)
#: What may come before a document's doctype: a BOM, whitespace, comments.
# (The page is worked on as latin-1, where a UTF-8 BOM reads as three characters.)
_DOCTYPE = re.compile(
    r"^(?:\ufeff|\u00ef\u00bb\u00bf)?(?:\s|<!--.*?-->)*<!doctype[^>]*>", re.IGNORECASE | re.DOTALL
)

#: The `Sec-Fetch-Dest` values of a frame loading a page — as opposed to a page
#: loading a script, a stylesheet or a `fetch`.
_NAVIGATION = frozenset({"iframe", "frame", "document"})

#: What a frame shows when it lands on something that is not there. A page, not a
#: bare "Not found.": it carries the runtime, and the runtime sees this marker and
#: tells the pane — which then stops remembering an address that is gone.
_MISSING_PAGE = (
    '<!doctype html><html><head><meta name="wui-missing" content="1">'
    '<title>Not found</title></head><body style="font-family:sans-serif;padding:24px">'
    "<p>This page is not part of the site, or it no longer exists.</p>"
    "<p>Use your browser's Back button to return.</p></body></html>"
)


#: The file the frontend build writes the runtime to (`web/vite-plugins/wuiRuntime.ts`).
RUNTIME_FILE = "wui-runtime.js"


def built_wui_runtime(spa_dist: Path) -> Callable[[], str | None]:
    """The runtime as the frontend build emitted it, or ``None`` with no build.

    Read on demand, not at boot: a dev server started before `pnpm build`
    picks the file up once it exists, and a rebuilt bundle is served without a
    restart. Held while the file's mtime is unchanged, because every page and
    every ping asks."""
    path = spa_dist / RUNTIME_FILE
    held: dict[str, object] = {}

    def read() -> str | None:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        if held.get("mtime") != mtime:
            held["text"] = path.read_text(encoding="utf-8")
            held["mtime"] = mtime
        text = held["text"]
        assert isinstance(text, str)  # narrow for ty
        return text

    return read


class OpaqueOriginGuard:
    """Refuse the rest of the API to a served page.

    A served page may send requests to its own host (`connect-src 'self'` — it
    needs its own folder's files), and a request with no session is not
    necessarily anonymous: the default composition answers EVERY request as the
    configured user, and a deploy's cookie reader may see a cookie a browser
    sends without `SameSite`. So a page could otherwise make blind calls to any
    route — start a run, cancel one — as the person viewing it, past every gate
    the bridge applies.

    What only such a page sends is `Origin: null`: an opaque origin stamps it on
    every `fetch`, XHR and non-GET request (measured, Phase 1). The app's own
    requests carry a real origin or none. So `/api/*` answers `Origin: null`
    with 403 — except the content route, which serves the page its own files.
    A pure ASGI middleware, like `VersionHeaderMiddleware`, so streaming bodies
    are untouched."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and _from_opaque_page(scope):
            path = scope["path"]
            root = scope.get("root_path", "")
            if root and path.startswith(root):
                path = path[len(root) :]
            if path.startswith("/api/") and not path.startswith("/api/wui-content/"):
                await PlainTextResponse("A page may not call this.", status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def _from_opaque_page(scope: dict[str, Any]) -> bool:
    return any(k == b"origin" and v == b"null" for k, v in scope.get("headers", []))


class WuiPass(Struct):
    """One minted pass. ``resource_id`` is the token itself."""

    slug: str
    item_id: str
    folder: str
    user: str
    expires_at: int


def register_wui_pass(spec: SpecStar) -> None:
    """Idempotently register the model, post-``spec.apply`` so its auto-CRUD
    routes stay unemitted — listing the passes would hand out every token."""
    with contextlib.suppress(ValueError):
        spec.add_model(WuiPass, indexed_fields=["user", "item_id", "folder", "expires_at"])


class WuiPasses:
    """Mint and look up passes."""

    def __init__(self, spec: SpecStar, *, now: Callable[[], float]) -> None:
        self._spec = spec
        self._now = now
        # token -> (pass, when it was read). A page asks for dozens of files at
        # once and each request presents the same pass; a specstar `get` is a
        # synchronous round trip on the event loop. Held briefly — a pass never
        # changes after it is minted, and its expiry is still judged per request.
        self._held: dict[str, tuple[WuiPass, float]] = {}

    def _rm(self):
        return self._spec.get_resource_manager(WuiPass)

    def mint(self, *, slug: str, item_id: str, folder: str, user: str) -> str:
        """A pass for this user's view of this folder — the one they already
        hold while it has time left, so opening a pane twenty times does not
        write twenty rows."""
        now = int(self._now())
        rm = self._rm()
        held = (
            (QB["user"] == user)
            & (QB["item_id"] == item_id)
            & (QB["folder"] == folder)
            & (QB["expires_at"] > now + _REUSE_MARGIN_S)
        )
        for res in rm.list_resources(held.limit(1).build()):
            return res.info.resource_id
        self._prune(now)
        token = secrets.token_urlsafe(32)
        rm.create(
            WuiPass(
                slug=slug, item_id=item_id, folder=folder, user=user, expires_at=now + PASS_TTL_S
            ),
            resource_id=token,
        )
        return token

    def _prune(self, now: int) -> None:
        rm = self._rm()
        lapsed = (QB["expires_at"] <= now).limit(_PRUNE_BATCH).build()
        for res in rm.list_resources(lapsed):
            with contextlib.suppress(ResourceIDNotFoundError):
                rm.permanently_delete(res.info.resource_id)

    def get(self, token: str) -> WuiPass | None:
        """The live pass behind ``token``, or ``None`` — unknown and lapsed are
        one answer, so a probe learns nothing about which tokens ever existed."""
        now = self._now()
        held = self._held.get(token)
        if held is not None and now - held[1] < _HOLD_S:
            data = held[0]
        else:
            try:
                found = self._rm().get(token).data
            except ResourceIDNotFoundError:
                return None
            assert isinstance(found, WuiPass)  # narrow for ty (coverage-clean)
            data = found
            if len(self._held) > _HOLD_MAX:  # bounded: a cache, not a map
                self._held.clear()
            self._held[token] = (data, now)
        return data if data.expires_at > int(now) else None


class PassBody(BaseModel):
    """The page's folder, workspace-absolute (`/` for a page at the root)."""

    folder: str


class PassOut(BaseModel):
    """Where the frame loads from: ``base`` + the workspace path, minus its
    leading slash. API-relative, like every route path the frontend hands to
    `apiFetch`, so a deploy under a sub-path prefixes it the same way."""

    base: str


def _inside(folder: str, path: str) -> bool:
    """Is workspace path ``path`` inside ``folder``? Compared on segments: a
    ``startswith`` says ``/docs2`` is inside ``/docs``. The folder is inside
    itself: it is the page's home, which its pages link back to as `../`."""
    return folder == "/" or path == folder or path.startswith(folder + "/")


_SCRIPT_TAG = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_HAS_SRC = re.compile(r"\bsrc\s*=", re.IGNORECASE)
_HAS_CROSSORIGIN = re.compile(r"\bcrossorigin\b", re.IGNORECASE)


def fetch_scripts_in_cors_mode(html: str) -> str:
    """Mark the page's `<script src>` tags `crossorigin="anonymous"`.

    In an opaque-origin document a plain `<script src>` is a cross-origin
    script, and the browser MUTES its errors: the report panel got "Script
    error." with no message, file or line, and the script's promise rejections
    did not arrive at all (measured in review). Fetched in CORS mode — which
    this route allows, `Access-Control-Allow-Origin: *` — they report in full.
    A tag that already says what it wants keeps it."""

    def mark(m: re.Match[str]) -> str:
        attrs = m.group(1)
        if not _HAS_SRC.search(attrs) or _HAS_CROSSORIGIN.search(attrs):
            return m.group(0)
        return f'<script{attrs} crossorigin="anonymous">'

    return _SCRIPT_TAG.sub(mark, html)


def _ascii(text: str) -> str:
    """JavaScript source as ASCII, so it can be put into a page whatever that
    page's encoding is. Non-ASCII becomes `\\uXXXX` (a surrogate pair above the
    BMP), which means the same thing in a string, a regex or a comment."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            out.append(ch)
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04x}")
        else:
            code -= 0x10000
            out.append(f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}")
    return "".join(out)


def serve_page(data: bytes, runtime: str) -> tuple[bytes, str]:
    """A page's bytes with the runtime put in, and the media type to send.

    Worked on as latin-1 — one character per byte, so nothing the page wrote is
    re-encoded — with the runtime as ASCII. A UTF-8 page is labelled so; any
    other keeps no charset, so the page's own `<meta charset>` decides, as it
    would on any static server (relabelling a Big5 page UTF-8 turned its text
    into replacement characters)."""
    html = fetch_scripts_in_cors_mode(data.decode("latin-1"))
    body = inject_runtime(html, _ascii(runtime)).encode("latin-1")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return body, "text/html"
    return body, "text/html; charset=utf-8"


def inject_runtime(html: str, runtime: str) -> str:
    """Put the runtime as the head's first child — before every line the page
    wrote, so `window.workspace` exists when its first statement runs and the
    error capture listens before the failure it exists for. A document without
    a `<head>` tag still has a head (the parser makes one); the script then goes
    first in the document, which lands in the same place."""
    # The runtime is ours, but the escape costs nothing and keeps a future edit
    # with a `</script` in a string from closing its own tag.
    safe = runtime.replace("</script", "<\\/script")
    tag = f"<script>{safe}</script>"
    match = _HEAD.search(html) or _DOCTYPE.search(html)
    # Without a `<head>` tag the script goes first — but never before the
    # doctype: in front of it, the browser ignores it and renders in quirks mode.
    if match is None:
        return tag + html
    return html[: match.end()] + tag + html[match.end() :]


def register_wui_content_routes(
    app: FastAPI | APIRouter,
    *,
    locator: ItemLocator,
    files: WorkspaceFiles,
    passes: WuiPasses,
    get_user_id: Callable[[], str],
    runtime: Callable[[], str | None],
) -> None:
    """Mount the mint route and the content route."""

    @app.post("/a/{slug}/items/{item_id}/wui/pass")
    async def mint_wui_pass(slug: str, item_id: str, body: PassBody) -> PassOut:
        item = locator.require_access(slug, item_id, "read_content")
        folder = _workspace_path(body.folder)
        token = passes.mint(slug=slug, item_id=item, folder=folder, user=get_user_id())
        return PassOut(base=f"/wui-content/{token}/")

    def _headers() -> dict[str, str]:
        return {
            "Content-Security-Policy": CONTENT_CSP,
            # An opaque origin's `fetch` is cross-origin even to this host; the
            # token, not the origin, is the credential.
            "Access-Control-Allow-Origin": "*",
            # A pass is in the URL; a page's own requests must not repeat it to
            # anyone, and nothing here is worth a stale copy.
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }

    @app.get("/wui-content/{token}/{path:path}")
    async def wui_content(token: str, path: str, request: Request) -> Response:
        grant = passes.get(token)
        if grant is None:
            return PlainTextResponse(
                "This page's link has expired. Open the page again.", status_code=404
            )
        locator.require_access(grant.slug, grant.item_id, "read_content", user=grant.user)
        if path == "__wui/ping":
            if runtime() is None:
                return PlainTextResponse("no runtime", status_code=503, headers=_headers())
            return PlainTextResponse(PING_ANSWER, headers=_headers())
        try:
            norm = _workspace_path(path)
        except HTTPException:
            norm = ""

        def missing() -> Response:
            script = runtime()
            if request.headers.get("sec-fetch-dest") in _NAVIGATION and script is not None:
                return Response(
                    inject_runtime(_MISSING_PAGE, script),
                    status_code=404,
                    media_type="text/html; charset=utf-8",
                    headers=_headers(),
                )
            return PlainTextResponse("Not found.", status_code=404, headers=_headers())

        if not norm or not _inside(grant.folder, norm):
            return missing()
        # A directory URL means its index, the way every static server reads it
        # — a generator links `setup/`, never `setup/index.html`. The bare pass
        # address (`path == ""`) is the workspace root, a directory too.
        directory = path == "" or path.endswith("/")
        if directory:
            norm = f"{norm.rstrip('/')}/index.html"
        try:
            data = await files.read(grant.item_id, norm)
        except FileNotFound:
            # The directory named without its slash. Served in place, the page's
            # own `../assets/x.css` would resolve one level too high, so it is
            # sent to the slash instead — relative, so it holds behind any
            # proxy prefix.
            if not directory and await files.is_dir(grant.item_id, norm):
                # Quoted: a header is latin-1, and `說明/` raised there (500).
                leaf = quote(norm.rsplit("/", 1)[-1])
                return Response(status_code=301, headers={**_headers(), "Location": f"{leaf}/"})
            return missing()
        media = media_type_for(norm, data)
        if media.startswith("text/html"):
            script = runtime()
            if script is None:
                return PlainTextResponse("no runtime", status_code=503, headers=_headers())
            body, html_type = serve_page(data, script)
            # As a header, not `media_type`: Starlette appends `charset=utf-8`
            # to a bare `text/html`, which is the relabelling this avoids.
            return Response(body, headers={**_headers(), "Content-Type": html_type})
        if media.startswith("text/") or "javascript" in media:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                # The single-page way read a non-UTF-8 text file as latin-1;
                # labelled UTF-8 (the default for `text/*`) its characters
                # became U+FFFD.
                media = f"{media.split(';')[0]}; charset=iso-8859-1"
        return Response(data, media_type=media, headers=_headers())

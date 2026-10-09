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

from fastapi import APIRouter, FastAPI, HTTPException, Response
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
#: a fresh one, so an open pane never holds one about to lapse.
_REUSE_MARGIN_S = PASS_TTL_S // 2

#: How many lapsed passes one mint clears. Bounded so a mint never turns into a
#: sweep of the whole table; each mint clears a few, and lapsed rows are only
#: ever made at the rate passes are minted.
_PRUNE_BATCH = 50

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
        try:
            data = self._rm().get(token).data
        except ResourceIDNotFoundError:
            return None
        assert isinstance(data, WuiPass)  # narrow for ty (coverage-clean)
        return data if data.expires_at > int(self._now()) else None


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
    ``startswith`` says ``/docs2`` is inside ``/docs``."""
    return folder == "/" or path.startswith(folder + "/")


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
    match = _HEAD.search(html)
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
    async def wui_content(token: str, path: str) -> Response:
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
        if not norm or not _inside(grant.folder, norm):
            return PlainTextResponse("Not part of this page.", status_code=404, headers=_headers())
        # A directory URL means its index, the way every static server reads it
        # — a generator links `setup/`, never `setup/index.html`.
        if path.endswith("/"):
            norm = f"{norm}/index.html"
        try:
            data = await files.read(grant.item_id, norm)
        except FileNotFound:
            # The directory named without its slash. Served in place, the page's
            # own `../assets/x.css` would resolve one level too high, so it is
            # sent to the slash instead — relative, so it holds behind any
            # proxy prefix.
            if not path.endswith("/") and await files.is_dir(grant.item_id, norm):
                leaf = norm.rsplit("/", 1)[-1]
                return Response(status_code=301, headers={**_headers(), "Location": f"{leaf}/"})
            return PlainTextResponse("Not found.", status_code=404, headers=_headers())
        media = media_type_for(norm, data)
        if media.startswith("text/html"):
            script = runtime()
            if script is None:
                return PlainTextResponse("no runtime", status_code=503, headers=_headers())
            return Response(
                inject_runtime(data.decode("utf-8", errors="replace"), script),
                media_type="text/html; charset=utf-8",
                headers=_headers(),
            )
        return Response(data, media_type=media, headers=_headers())

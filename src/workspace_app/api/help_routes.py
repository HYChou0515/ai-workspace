"""#230: the ``/help`` endpoint.

Exposes the platform Help collection's id (so the FE can scope its KB chat to it)
plus the collection's documents (so the FE can link each to the existing KB
document viewer). It reuses the seeded "Platform Help" collection — ``ensure``
is idempotent, so the route works even in environments where the boot seed
hasn't run (the collection is created empty rather than 404ing). #281 will later
add source-code-derived wiki to the same collection.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from specstar import QB, SpecStar

from ..kb.changelog import Release, parse_changelog
from ..kb.help_collection import ensure_help_collection, help_content_dir
from ..resources.kb import SourceDoc


class HelpDocument(BaseModel):
    """One readable document in the Help collection. ``id`` is the opaque
    SourceDoc id the KB document viewer takes (``GET /kb/documents?id=``)."""

    id: str
    path: str
    title: str
    kind: str  # "release_notes" | "guide" — lets the FE group + localise labels


class HelpInfo(BaseModel):
    collection_id: str
    documents: list[HelpDocument]


class ReleasesInfo(BaseModel):
    """The CHANGELOG parsed into structured, newest-first releases for the web
    /help/releases view (#441)."""

    releases: list[Release]


def _kind(path: str) -> str:
    """Release notes live in CHANGELOG.md; everything else is a guide."""
    return "release_notes" if path.rsplit("/", 1)[-1].lower() == "changelog.md" else "guide"


def _title(path: str) -> str:
    """A human label derived from the filename stem (the FE may override known
    `kind`s with a localised label)."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    humanised = stem.replace("-", " ").replace("_", " ").strip()
    return humanised[:1].upper() + humanised[1:] if humanised else path


def register_help_routes(app: FastAPI | APIRouter, spec: SpecStar) -> None:
    @app.get("/help")
    async def help_info() -> HelpInfo:
        cid = ensure_help_collection(spec)
        rm = spec.get_resource_manager(SourceDoc)
        docs = [
            HelpDocument(
                id=r.info.resource_id,  # ty: ignore[unresolved-attribute]
                path=r.data.path,
                title=_title(r.data.path),
                kind=_kind(r.data.path),
            )
            for r in rm.list_resources((QB["collection_id"] == cid).build())
            if isinstance(r.data, SourceDoc)
        ]
        # Guides first, then release notes; stable by path within each group.
        docs.sort(key=lambda d: (d.kind == "release_notes", d.path))
        return HelpInfo(collection_id=cid, documents=docs)

    @app.get("/help/releases")
    async def help_releases() -> ReleasesInfo:
        # The packaged CHANGELOG.md (git-cliff output) is the source of truth and
        # ships with the wheel, so it is always readable — no KB round-trip.
        text = (help_content_dir() / "CHANGELOG.md").read_text(encoding="utf-8")
        return ReleasesInfo(releases=parse_changelog(text))

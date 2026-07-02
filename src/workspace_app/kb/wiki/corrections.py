"""Wiki corrections (#397) — the deterministic, LLM-free page I/O behind "tell
the wiki what's wrong instead of editing it yourself".

A user (directly, or an AI drafting on their behalf) submits a correction
directive: what is wrong, how it should read, optionally a reference document and
the page at fault. Two things happen (both regression-proof):

  1. the corrected FACT is appended here to a builder-immune ``/corrections/``
     page (one file per target wiki page, Q15), so a later rebuild can re-read
     the ground truth instead of reintroducing the error; and
  2. a ``correct`` wiki job is enqueued so the corrector agent applies it to the
     live pages (that half lives on the coordinator, which owns the queue).

Per Q9 the immune page records only the corrected fact + a note that a reference
was provided — never the reference's full text (that rides the job payload for
this pass alone).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .store import correction_page_path

if TYPE_CHECKING:
    from .store import WikiFileStore


class WikiNotEnabledError(Exception):
    """A wiki correction was submitted for a collection with no wiki (missing, or
    ``use_wiki`` off) — there is nothing to correct (#397 Q13). Callers map this to
    a friendly message (a tool error / an HTTP 4xx)."""


CORRECTIONS_HEADER = (
    "# Corrections\n\n"
    "User-reported corrections to this wiki (#397). Each entry is the corrected "
    "fact the wiki must reflect; a rebuild cannot overwrite this page.\n"
)


def render_correction_entry(
    *, instruction: str, target_page: str, actor: str, has_reference: bool
) -> str:
    """One faithful correction section: who reported it, which page it's about, the
    corrected fact verbatim, and (if any) a note that a reference backed it — never
    the reference text itself (#397 Q9)."""
    about = f" (page: {target_page})" if target_page.strip() else ""
    parts = ["\n---\n", f"\n**Correction{about}** — submitted by {actor}\n", f"\n{instruction}\n"]
    if has_reference:
        parts.append("\n_A reference document was provided with this correction._\n")
    return "".join(parts)


async def append_correction_page(
    wiki_store: WikiFileStore,
    *,
    collection_id: str,
    target_page: str,
    instruction: str,
    actor: str,
    has_reference: bool = False,
) -> str:
    """Append the corrected fact to the collection's immune corrections page for
    ``target_page`` (one file per target, ``general.md`` when unnamed — Q15) and
    return the page path. Uses the RAW store, so the human/tool path can write a
    page the corrector agent cannot (see ``MaintainerWikiStore``)."""
    path = correction_page_path(target_page)
    entry = render_correction_entry(
        instruction=instruction, target_page=target_page, actor=actor, has_reference=has_reference
    )
    prior = (
        await wiki_store.read(collection_id, path)
        if await wiki_store.exists(collection_id, path)
        else CORRECTIONS_HEADER.encode()
    )
    await wiki_store.write(collection_id, path, prior + entry.encode())
    return path

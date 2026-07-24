"""#633 — the names a question mentions, resolved without asking the model.

A 14B model decides whether to look something up by whether IT recognises the
word, not by whether the question is internal: 「回焊爐是什麼?」 answered from
general knowledge with the dossier tool one call away, four prompt attempts
running (#630 P7). Asking better was not working, so this stops asking.

Every shape here was forced by a measurement:

* **Scan the question for known names — do not tokenize the question.**
  Tokenizing is what you do when you don't know the vocabulary; we do. CJK has
  no word boundaries to split on either, so scanning is also the only thing that
  works on Chinese. Cost is a set intersection: 1–2 µs, independent of how big
  the graph is.
* **Hold the index in memory, keyed name → ids.** Asking the database for a name
  is a full scan of the list column (2849 ms at 40k rows); fetching by primary
  key afterwards is 0.3 ms. 100k names cost 22 MB — 2% of the pod's limit.
* **A name maps to MANY ids.** Two pods can mint an identity concurrently, a
  merge leaves a tombstone behind, and two collections can name different things
  alike. The value is always a tuple, never "a string, or a tuple when there are
  several" — two shapes for one field guarantee a reader somewhere forgets one.

The boundary rule and normalisation are the glossary's (#106), not new ones:
they already handle `m4` not firing inside `m40` while letting CJK match
embedded, and they have been in production for a while.
"""

from __future__ import annotations

from ..context_cards import mentions, norm

# A one-character name fires on nearly every question. The floor belongs to the
# index rather than to each lookup, so nothing downstream can forget to apply it.
MIN_NAME_LEN = 2

# The longest name the scanner will consider. Only used to bound candidate
# generation when a caller wants candidates rather than a scan.
MAX_NAME_LEN = 24


class NameIndex:
    """Known names → the identities they resolve to.

    Immutable once built. ``dropped`` records how many names a ``limit`` left
    out: a cap that truncates silently reads as "we covered everything", and
    here it must not, because the operator's only signal that auto-injection has
    stopped being complete is this number. Whatever is dropped is still
    reachable — the agent's dossier tool queries the database directly.
    """

    __slots__ = ("_by_name", "_longest", "dropped")

    def __init__(self, names: dict[str, tuple[str, ...]], *, limit: int | None = None) -> None:
        kept: dict[str, tuple[str, ...]] = {}
        dropped = 0
        for name, ids in names.items():
            key = norm(name)
            if len(key) < MIN_NAME_LEN or not ids:
                dropped += 1
                continue
            if limit is not None and len(kept) >= limit and key not in kept:
                dropped += 1
                continue
            kept[key] = tuple(dict.fromkeys(kept.get(key, ()) + tuple(ids)))
        self._by_name = kept
        self.dropped = dropped
        # Bound candidate generation by the longest name actually held, not by a
        # constant: a corpus of short names must not pay for a long one nobody
        # indexed. Capped, so one pathological name cannot widen every lookup.
        self._longest = min(max((len(k) for k in kept), default=MIN_NAME_LEN), MAX_NAME_LEN)

    def __len__(self) -> int:
        return len(self._by_name)

    def hits(self, text: str) -> dict[str, tuple[str, ...]]:
        """Every indexed name the text mentions, with the identities it resolves
        to. Ambiguity is carried, never collapsed to a first match.

        Two steps, and the order is the point. First a SET INTERSECTION between
        the question's substrings and the indexed names — hash work bounded by
        the QUESTION's length, so a graph of 500k names costs what one of 500
        costs. Scanning the names instead would be O(names) on every message,
        which is the cost this module exists to avoid.

        Then the boundary rule, applied to the survivors only. Intersection
        alone would match "m4" inside "m40" — set membership knows nothing about
        word edges — and there are normally zero to three survivors to check.
        """
        nt = norm(text)
        if not nt:
            return {}
        candidates = {
            nt[i : i + k]
            for i in range(len(nt))
            for k in range(MIN_NAME_LEN, self._longest + 1)
            if i + k <= len(nt)
        }
        return {
            name: self._by_name[name]
            for name in candidates & self._by_name.keys()
            if mentions(nt, name)
        }

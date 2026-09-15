"""#88: a cheap, CJK-aware token estimate for a document's *extracted text*.

The KB UI shows an "≈ N tokens" figure for a collection. It used to be derived
from the raw upload size (``blob_bytes / 4``), which is wildly wrong for binary
formats — a 10 MB PDF whose extracted text is 50 KB still reads as ~2.5 M
tokens. Issue #88: base the figure on the text we actually chunk and embed.

The estimate stays an *approximation* (hence the "≈" in the UI), but a far
better one: CJK scripts pack roughly one token per character, while Latin text
runs ~4 characters per token. So we count CJK characters as one token each and
divide the remaining characters by four. This avoids a real tokenizer (we run
several swappable models via Ollama; none ships an offline tokenizer here) while
handling the Traditional-Chinese-heavy corpora this KB is built for."""

import re

#: The CJK scripts counted as ~1 token/char - Chinese ideographs (incl. Ext-A /
#: Ext-B / compatibility), Japanese kana, Korean Hangul - as the BODY of a regex
#: character class (no brackets) so other modules can compose it. CJK
#: punctuation/symbols are deliberately excluded: they tokenise more like Latin
#: punctuation, so they fall into the ``/4`` bucket. The chunker builds its token
#: pattern from this, so "what counts as one CJK token" is defined ONCE. Two
#: copies of this list drifted before: the estimate here counted CJK per
#: character while the chunker counted `\S+` runs, so a Chinese document read
#: as ~12k tokens and came out of the chunker as ONE chunk (plan-rag-context P1).
CJK_RANGES = (
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\u3400-\u4dbf"  # CJK Ext-A
    "\U00020000-\U0002a6df"  # CJK Ext-B
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "\u3040-\u30ff"  # Hiragana + Katakana
    "\uac00-\ud7af"  # Hangul Syllables
)

#: Counting via the regex engine instead of a per-character Python generator is
#: ~5x faster on a long string and returns identical counts (#624: the chat
#: history budget now calls this on every turn, on the event loop, so the
#: per-character loop became a measurable stall on long threads).
_CJK_RE = re.compile(f"[{CJK_RANGES}]")


def count_tokens(text: str) -> int:
    """Estimate the LLM token count of *text* (``0`` for empty/blank).

    ``tokens = cjk_chars + round(non_cjk_chars / 4)`` — see the module docstring
    for the rationale."""
    cjk = len(_CJK_RE.findall(text))
    non_cjk = len(text) - cjk
    return cjk + round(non_cjk / 4)

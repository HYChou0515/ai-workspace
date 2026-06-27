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


def _is_cjk(ch: str) -> bool:
    """Whether *ch* belongs to a CJK script counted as ~1 token/char: Chinese
    ideographs (incl. Ext-A / Ext-B / compatibility), Japanese kana, and Korean
    Hangul. CJK punctuation/symbols are deliberately excluded — they tokenise
    more like Latin punctuation, so they fall into the ``/4`` bucket."""
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF  # CJK Ext-A
        or 0x20000 <= cp <= 0x2A6DF  # CJK Ext-B
        or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3040 <= cp <= 0x30FF  # Hiragana + Katakana
        or 0xAC00 <= cp <= 0xD7AF  # Hangul Syllables
    )


def count_tokens(text: str) -> int:
    """Estimate the LLM token count of *text* (``0`` for empty/blank).

    ``tokens = cjk_chars + round(non_cjk_chars / 4)`` — see the module docstring
    for the rationale."""
    cjk = sum(1 for ch in text if _is_cjk(ch))
    non_cjk = len(text) - cjk
    return cjk + round(non_cjk / 4)

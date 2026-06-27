"""#88: chunk-based token estimate. ``count_tokens`` approximates the LLM
token footprint of a document's *extracted text* (not its raw blob bytes) with
a CJK-aware heuristic: each CJK character counts as ~1 token, everything else
(Latin / digits / punctuation / whitespace) as ~4 chars per token."""

from workspace_app.kb.tokens import count_tokens


def test_mixed_cjk_and_ascii() -> None:
    # 2 CJK chars → 2 tokens; " world" is 6 non-CJK chars → round(6/4)=2.
    assert count_tokens("你好 world") == 4


def test_pure_cjk_is_one_token_per_char() -> None:
    assert count_tokens("資料科學") == 4


def test_pure_ascii_is_four_chars_per_token() -> None:
    assert count_tokens("abcdefgh") == 2  # 8 / 4
    assert count_tokens("hello world") == 3  # round(11 / 4) = round(2.75)


def test_empty_and_blank_are_zero() -> None:
    assert count_tokens("") == 0
    assert count_tokens("   ") == 1  # 3 whitespace chars → round(3/4) = 1


def test_cjk_punctuation_counts_as_non_cjk() -> None:
    # 。(U+3002) is CJK punctuation, not an ideograph → it falls in the /4 bucket.
    assert count_tokens("。。。。") == 1


def test_japanese_kana_and_korean_hangul_are_cjk() -> None:
    assert count_tokens("こんにちは") == 5  # hiragana
    assert count_tokens("안녕하세요") == 5  # hangul

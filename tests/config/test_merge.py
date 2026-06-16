"""Layered config merge — `merge_layered(base, override)`.

The loader applies this twice in our pipeline:

1. Bundled defaults ◇ operator's `config.yaml` — operator only writes
   the deltas (Q7: "全可設定但保留預設值,不用真的全部都給").
2. Preset config ◇ usage (`workspace_chat[]`, `kb_chat`, template
   `_config.json`) — usage references a preset by name and overrides
   only what differs (Q5).

Merge rules (one shape, applied at every nesting level):

- **Scalar** (str / int / float / bool / None) — override replaces base
- **List** — override REPLACES the whole list (no append/dedup; Q5
  rationale: append semantics traps templates that want to subtract a
  tool from preset; replace gives full control)
- **Dict (mapping)** — shallow merge: keys in override replace base's
  same key, keys only in base survive. RECURSIVE into nested dicts so
  `kb.embedder.model` can be overridden without restating
  `kb.embedder.timeout`.
- **Type mismatch** (base says int, override says str) — override wins
  verbatim; the schema validator (later stage) catches type errors with
  the field path in the message.

Pure function: no I/O, no globals, no env reads.
"""

from __future__ import annotations

from workspace_app.config.merge import merge_layered


def test_empty_override_returns_a_copy_of_base():
    """No override → base is returned (deep-copied so mutating result
    can't change base)."""
    base = {"a": 1, "b": [1, 2]}
    out = merge_layered(base, {})
    assert out == {"a": 1, "b": [1, 2]}
    out["b"].append(99)
    assert base["b"] == [1, 2]  # base untouched


def test_scalar_override_replaces_base_value():
    assert merge_layered({"port": 8000}, {"port": 9000}) == {"port": 9000}


def test_override_adds_a_new_key_not_in_base():
    """Override may introduce keys base didn't have."""
    assert merge_layered({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


def test_list_override_replaces_the_whole_list():
    """allowed_tools / suggestions are lists — override REPLACES (Q5).
    The user gets the exact set they wrote, no append surprises."""
    assert merge_layered(
        {"allowed_tools": ["exec", "read_file"]},
        {"allowed_tools": ["kb_search"]},
    ) == {"allowed_tools": ["kb_search"]}


def test_list_override_to_empty_clears_the_base_list():
    """Operator writing `allowed_tools: []` means "no tools" — must
    win over base's list, not silently extend."""
    assert merge_layered({"allowed_tools": ["exec"]}, {"allowed_tools": []}) == {
        "allowed_tools": []
    }


def test_dict_override_does_shallow_per_key_merge():
    """`kb.embedder.model` override leaves `kb.embedder.timeout` alone."""
    assert merge_layered(
        {"kb": {"embedder": {"model": "bge-m3", "timeout": 60.0}}},
        {"kb": {"embedder": {"model": "openai/text-embedding-3-small"}}},
    ) == {"kb": {"embedder": {"model": "openai/text-embedding-3-small", "timeout": 60.0}}}


def test_dict_override_merge_recurses_deeply():
    """3+ levels: override at the leaf only touches that leaf."""
    base = {"a": {"b": {"c": 1, "d": 2}, "e": 3}}
    over = {"a": {"b": {"c": 99}}}
    assert merge_layered(base, over) == {"a": {"b": {"c": 99, "d": 2}, "e": 3}}


def test_override_adds_a_new_dict_key_alongside_inherited_ones():
    base = {"kb": {"embedder": {"model": "bge-m3"}}}
    over = {"kb": {"chunker": {"max_tokens": 512}}}
    assert merge_layered(base, over) == {
        "kb": {"embedder": {"model": "bge-m3"}, "chunker": {"max_tokens": 512}}
    }


def test_dict_replaces_scalar_when_types_disagree():
    """If base has a scalar and override gives a dict at the same key,
    override wins verbatim. Schema validator (later stage) decides
    whether that's legal for this field."""
    assert merge_layered({"x": 1}, {"x": {"nested": True}}) == {"x": {"nested": True}}


def test_scalar_replaces_dict_when_types_disagree():
    """Inverse of the above — the validator catches schema mismatch."""
    assert merge_layered({"x": {"nested": True}}, {"x": 1}) == {"x": 1}


def test_list_replaces_dict_when_types_disagree():
    assert merge_layered({"x": {"a": 1}}, {"x": [1, 2]}) == {"x": [1, 2]}


def test_none_in_override_replaces_the_base_value():
    """Override = explicit None (e.g. `pg_dsn: null`) wipes the base
    value to None — same rule as any other scalar."""
    assert merge_layered({"pg_dsn": "postgresql://..."}, {"pg_dsn": None}) == {"pg_dsn": None}


def test_nested_list_is_replaced_not_merged():
    """List as a value inside a nested dict is a leaf — replace, not append."""
    base = {"agents": {"workspace_chat": [{"preset": "a"}]}}
    over = {"agents": {"workspace_chat": [{"preset": "b"}, {"preset": "c"}]}}
    assert merge_layered(base, over) == {
        "agents": {"workspace_chat": [{"preset": "b"}, {"preset": "c"}]}
    }


def test_base_is_not_mutated_by_the_merge():
    """Pure function — caller's base dict survives intact."""
    base = {"a": {"b": 1}}
    over = {"a": {"b": 2, "c": 3}}
    merge_layered(base, over)
    assert base == {"a": {"b": 1}}  # untouched

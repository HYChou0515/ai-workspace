"""#513 P1 — defect library scope-chain resolution over context cards.

A defect entry is a ``ContextCard`` whose keys are scope-qualified
(``<scope>|<code>``: machine / station-type / layer). ``resolve`` walks a
caller-supplied scope chain (specific → broad) and returns the most-specific
card for a code, so shared knowledge lives once at type/layer level while a
machine can override it.
"""

from workspace_app.kb.context_cards import derive_norm_keys
from workspace_app.kb.defect_library import resolve, scope_key
from workspace_app.resources import make_spec
from workspace_app.resources.kb import Collection, ContextCard


def _collection(spec, name: str = "defects") -> str:
    return spec.get_resource_manager(Collection).create(Collection(name=name)).resource_id


def _card(spec, cid: str, keys: list[str], **kw) -> str:
    rm = spec.get_resource_manager(ContextCard)
    card = ContextCard(collection_id=cid, keys=keys, norm_keys=derive_norm_keys(keys), **kw)
    return rm.create(card).resource_id


def test_resolve_finds_card_by_scope_qualified_key():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    rid = _card(spec, cid, [scope_key("etch", "M4")], body="bridge on metal-4 at etch")
    got = resolve(spec, cid, "M4", ["etch"])
    assert got is not None
    rid_got, card = got
    assert rid_got == rid
    assert card.body == "bridge on metal-4 at etch"


def test_resolve_prefers_the_most_specific_scope():
    # A machine-specific card overrides the shared station-type card for the
    # same code, because the machine scope comes first in the chain.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, [scope_key("etch", "M4")], body="shared etch knowledge")
    _card(spec, cid, [scope_key("etchtool07", "M4")], body="tool-07 special")
    got = resolve(spec, cid, "M4", ["etchtool07", "etch"])
    assert got is not None
    assert got[1].body == "tool-07 special"


def test_resolve_falls_back_to_a_broader_scope():
    # No machine-specific card → the shared station-type card still answers.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, [scope_key("etch", "M4")], body="shared etch knowledge")
    got = resolve(spec, cid, "M4", ["etchtool07", "etch"])
    assert got is not None
    assert got[1].body == "shared etch knowledge"


def test_resolve_falls_back_to_a_global_bare_code_card():
    # A card keyed by the bare code (no scope) is the broadest fallback — it
    # answers when nothing in the scope chain carries the code.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, ["M4"], body="applies everywhere")
    got = resolve(spec, cid, "M4", ["etchtool07", "etch"])
    assert got is not None
    assert got[1].body == "applies everywhere"


def test_resolve_returns_none_when_no_scope_carries_the_code():
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, [scope_key("etch", "M4")], body="only at etch")
    assert resolve(spec, cid, "M4", ["litho"]) is None  # unknown scope
    assert resolve(spec, cid, "P2", ["etch"]) is None  # unknown code


def test_resolve_is_scoped_to_the_collection():
    spec = make_spec(default_user="u")
    a, b = _collection(spec, "a"), _collection(spec, "b")
    _card(spec, a, [scope_key("etch", "M4")], body="in-a")
    assert resolve(spec, b, "M4", ["etch"]) is None  # other collection's card excluded


def test_resolve_matches_the_whole_code_not_a_prefix():
    # The exact-membership guard (#104/#181) survives scope qualification:
    # scope "etch" + code "M4" must not resolve an "etch|M40" card.
    spec = make_spec(default_user="u")
    cid = _collection(spec)
    _card(spec, cid, [scope_key("etch", "M40")], body="metal-40")
    assert resolve(spec, cid, "M4", ["etch"]) is None

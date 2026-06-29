"""Parser config precedence resolution (#328).

A prompt/param-driven parser's effective config is a three-layer merge:
parser-declared defaults < per-collection config < per-doc override,
merged PER KNOB so an override can change one knob and inherit the rest.
``effective_config`` is the single resolver the ingestor (normal index)
and the dry-run re-parse (findability modal preview) both call.
"""

from __future__ import annotations

from workspace_app.kb.parser_config import effective_config
from workspace_app.kb.parsers.protocol import IParser, ParamSpec


class _Ontology(IParser):
    """A stand-in prompt-driven parser declaring two knobs."""

    def config_fields(self):
        return [
            ParamSpec("prompt", "text", "Extraction prompt", default="DEFAULT PROMPT"),
            ParamSpec("max_depth", "number", "Max ontology depth", default=3),
        ]

    def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
        return True

    def parse(self, source, *, filename, mime, **kw):  # type: ignore[no-untyped-def]
        return []


def test_doc_override_beats_collection_beats_default_per_knob():
    """The per-doc override wins; the collection layer wins over the
    parser default; an unset knob falls through to the layer below — all
    resolved per knob (overriding ``prompt`` leaves ``max_depth`` on the
    collection value)."""
    eff = effective_config(
        _Ontology(),
        collection_configs={"_Ontology": {"prompt": "COLLECTION PROMPT", "max_depth": 5}},
        doc_overrides={"_Ontology": {"prompt": "DOC PROMPT"}},
    )
    assert eff == {"prompt": "DOC PROMPT", "max_depth": 5}


def test_defaults_used_when_nothing_stored():
    """No collection config and no per-doc override (both ``None``) ⇒ the
    parser's declared defaults verbatim."""
    assert effective_config(_Ontology()) == {"prompt": "DEFAULT PROMPT", "max_depth": 3}


class _NoKnobs(IParser):
    def matches(self, *, filename, mime, source):  # type: ignore[no-untyped-def]
        return True

    def parse(self, source, *, filename, mime, **kw):  # type: ignore[no-untyped-def]
        return []


def test_parser_without_knobs_resolves_empty():
    """A parser that declares no ``config_fields`` has no effective config
    — even a (defensively) stored stale dict under its name is dropped, so
    ``parse`` never sees a knob the parser doesn't own."""
    assert effective_config(_NoKnobs(), collection_configs={"_NoKnobs": {"x": 1}}) == {}


def test_undeclared_stored_keys_are_dropped():
    """A value stored for a knob the parser no longer declares (renamed /
    removed) does NOT leak into the effective config."""
    eff = effective_config(
        _Ontology(),
        collection_configs={"_Ontology": {"prompt": "P", "since_removed": "stale"}},
    )
    assert eff == {"prompt": "P", "max_depth": 3}

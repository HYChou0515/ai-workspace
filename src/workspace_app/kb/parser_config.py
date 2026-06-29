"""Parser config precedence resolution (#328).

A prompt/param-driven :class:`~workspace_app.kb.parsers.protocol.IParser`
(e.g. an ontology extractor whose prompt carries the target JSON schema)
gets its effective config from a three-layer merge:

    parser-declared defaults  <  per-collection config  <  per-doc override

merged **per knob** — a per-doc override can change one knob and inherit
the rest. This is the single resolver both the ingestor (normal index)
and the dry-run re-parse (findability-probe preview, #328) call, so the
precedence rule lives in exactly one place.

The parser is keyed by ``type(parser).__name__`` — the same id the
ingestor stamps onto ``DocChunk.parser_id`` — so the stored
``Collection.parser_configs`` / ``SourceDoc.parser_config_overrides``
dicts address each parser's config by that name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .parsers.protocol import IParser


def parser_id(parser: IParser) -> str:
    """The stable key a parser's stored config is filed under — its class
    name, matching ``DocChunk.parser_id`` (set by the ingestor)."""
    return type(parser).__name__


def effective_config(
    parser: IParser,
    *,
    collection_configs: Mapping[str, Mapping[str, Any]] | None = None,
    doc_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The config to pass into ``parser.parse(config=...)``: parser
    defaults overlaid by the collection's config for this parser, then by
    the per-doc override — a shallow (per-knob) merge.

    ``collection_configs`` / ``doc_overrides`` are the whole
    ``parser_id -> {knob: value}`` maps stored on the Collection / SourceDoc
    (``None`` ⇒ none set).

    The result is restricted to the parser's **declared** knobs (every
    ``ParamSpec`` carries a default, so the declared set is exactly
    ``defaults.keys()``): a stored value for an undeclared key — e.g. a
    knob since renamed/removed from ``config_fields`` — is dropped rather
    than leaked into ``parse``. A parser with no ``config_fields`` always
    resolves to ``{}``."""
    pid = parser_id(parser)
    defaults = {f.key: f.default for f in parser.config_fields()}
    coll = dict((collection_configs or {}).get(pid, {}))
    doc = dict((doc_overrides or {}).get(pid, {}))
    merged = {**defaults, **coll, **doc}
    return {key: merged[key] for key in defaults}

"""Read a `view: chart` file the way the host reads it, and check it.

Two readers see every chart file: the SPA (js-yaml, then the renderer) and this
package (`validate`, then `query`). They must see the SAME document, or the
sandbox highlights one set of rows while the chart draws another.

PyYAML reads YAML 1.1: `no` is False, `017` is 15, `12:30` is 750, `<<` merges.
The host's js-yaml reads the YAML 1.2 core schema, where every one of those is
something else. So this module does not use PyYAML's resolvers at all: `_Loader`
starts from PyYAML's parser with the implicit resolvers replaced by the 1.2 core
ones, merge keys left as plain keys, and duplicate keys refused (js-yaml throws
on them; PyYAML silently keeps the last). The corpus in `spec-corpus/` pins the
result against js-yaml itself.

The schema is one file, `spec.schema.json`, beside this module; the renderer
imports the same file. Nothing here restates it.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match
from jsonschema.protocols import Validator
from yaml.constructor import ConstructorError

_NULL = re.compile(r"^(?:~|null|Null|NULL|)$")
_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_INT = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_FLOAT = re.compile(
    r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
)

_TAG_NULL = "tag:yaml.org,2002:null"
_TAG_BOOL = "tag:yaml.org,2002:bool"
_TAG_INT = "tag:yaml.org,2002:int"
_TAG_FLOAT = "tag:yaml.org,2002:float"


class SpecError(ValueError):
    """The file is not a readable YAML document."""


class _Loader(yaml.SafeLoader):
    """PyYAML's parser with YAML 1.2 core scalar resolution (see module doc)."""

    yaml_implicit_resolvers: dict[str | None, list[tuple[str, re.Pattern[str]]]] = {}

    def flatten_mapping(self, node: yaml.MappingNode) -> None:
        # `<<` is an ordinary key under the core schema; js-yaml keeps it as one.
        return None

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ConstructorError(
                    None, None, f"duplicated mapping key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _construct_bool(loader: _Loader, node: yaml.ScalarNode) -> bool:
    return loader.construct_scalar(node).lower() == "true"


def _construct_int(loader: _Loader, node: yaml.ScalarNode) -> int:
    text = str(loader.construct_scalar(node))
    if text.startswith("0o"):
        return int(text[2:], 8)
    if text.startswith("0x"):
        return int(text[2:], 16)
    # Leading zeros are decimal under 1.2 (`017` is 17), and int() agrees.
    return int(text)


def _construct_float(loader: _Loader, node: yaml.ScalarNode) -> float:
    text = str(loader.construct_scalar(node))
    lowered = text.lower()
    if lowered.endswith(".inf"):
        return float("-inf") if text.startswith("-") else float("inf")
    if lowered == ".nan":
        return float("nan")
    return float(text)


_Loader.add_implicit_resolver(_TAG_NULL, _NULL, ["~", "n", "N", ""])
_Loader.add_implicit_resolver(_TAG_BOOL, _BOOL, list("tTfF"))
_Loader.add_implicit_resolver(_TAG_INT, _INT, list("-+0123456789"))
_Loader.add_implicit_resolver(_TAG_FLOAT, _FLOAT, list("-+.0123456789"))
_Loader.add_constructor(_TAG_BOOL, _construct_bool)
_Loader.add_constructor(_TAG_INT, _construct_int)
_Loader.add_constructor(_TAG_FLOAT, _construct_float)


def parse_spec(text: str) -> Any:
    """The document the host sees for this file's text. Raises `SpecError`."""
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506 — _Loader is a SafeLoader
    except yaml.YAMLError as e:
        raise SpecError(f"not valid YAML: {e}") from e


@cache
def spec_schema() -> dict[str, Any]:
    """The schema file both readers share."""
    return json.loads(resources.files("chart_view").joinpath("spec.schema.json").read_text())


@cache
def _validator() -> Validator:
    return Draft202012Validator(spec_schema())


def _where(error: ValidationError) -> str:
    path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
    return path.lstrip(".") or "(top level)"


def _resolve(schema: Any) -> Any:
    # Every `$ref` in the schema is local (`#/$defs/<name>`).
    while isinstance(schema, dict) and "$ref" in schema:
        schema = spec_schema()["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def _accepts_type(schema: Any, instance: Any) -> bool:
    declared = schema.get("type") if isinstance(schema, dict) else None
    if declared is None:
        return True
    kinds = [declared] if isinstance(declared, str) else declared
    return any(_validator().is_type(instance, kind) for kind in kinds)


def _meant(error: ValidationError) -> list[ValidationError]:
    """The failed alternative the author was writing, as its own errors.

    For a mapping, that is the alternative whose keys it shares most — a
    `{diff, aggregate}` transform is a diff with a missing side, not an
    aggregate with a stray key. For anything else, the alternatives that take
    its JSON type: a string `source:` is a bad file name, not a bad mapping.
    """
    context = list(error.context or [])
    assert isinstance(error.schema, dict)
    alternatives = [_resolve(a) for a in error.schema["oneOf"]]
    if isinstance(error.instance, dict):

        def shared(index: int) -> int:
            props = alternatives[index].get("properties", {})
            return len(props.keys() & error.instance.keys())

        picked = {max(range(len(alternatives)), key=lambda i: (shared(i), -i))}
    else:
        picked = {i for i, a in enumerate(alternatives) if _accepts_type(a, error.instance)}
    return [e for e in context if e.relative_schema_path[0] in picked] or context


def _stated(error: ValidationError) -> str | None:
    """The schema's own sentence for this failure, when it has one.

    `errorMessage: {<keyword>: <sentence>}` — the ajv-errors convention, so the
    renderer can show the same sentence from the same file.
    """
    messages = error.schema.get("errorMessage") if isinstance(error.schema, dict) else None
    if isinstance(messages, dict) and isinstance(error.validator, str):
        stated = messages.get(error.validator)
        return str(stated) if stated else None
    return None


def _explain(error: ValidationError) -> ValidationError:
    """The error worth showing: a failed `oneOf` says only "not valid under any
    of the given schemas", so show the error inside the alternative the author
    meant instead — unless the schema states the rule itself."""
    while error.validator == "oneOf" and error.context and _stated(error) is None:
        error = best_match(_meant(error))
    return error


def spec_errors(doc: Any) -> list[str]:
    """Every way `doc` breaks the schema, one line each; empty means valid."""
    lines: list[str] = []
    for error in sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path)):
        shown = _explain(error)
        stated = _stated(shown)
        if stated is None:
            lines.append(f"{_where(shown)}: {shown.message}")
        elif isinstance(shown.instance, dict | list):
            lines.append(f"{_where(shown)}: {stated}")
        else:
            lines.append(f"{_where(shown)}: {shown.instance!r} — expected {stated}")
    return lines

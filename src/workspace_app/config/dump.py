"""Render a resolved `Settings` + provenance as annotated YAML — the startup
config dump (observability feature A).

The operator's pain is "I can't tell what I set from what defaulted", so every
leaf is emitted as `key: value  # ← <source>` where source is `config.yaml`,
`env`, or `default`. The output is valid, re-loadable YAML (the comments are
ignored on load), so the on-disk copy doubles as a reproducer of the run.

`reveal_secrets` is the one knob between the two surfaces:
- file (0600): `reveal_secrets=True` — real values, a faithful reproducer.
- stdout: `reveal_secrets=False` — secrets shown as `*** set (N chars) ***` /
  `*** unset ***` (plus `via ${VAR}` when env-sourced) so it's paste-safe.

`base_url` is deliberately NOT a secret — the operator wants to see where it
calls.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import IO, Any

from .loader import SOURCE_ENV, Provenance, Source
from .schema import Settings

# The resolved-config filename, written next to the operator's config.yaml.
RESOLVED_FILENAME = "config.resolved.yaml"

# A scalar string safe to emit bare (no YAML quoting needed). Anything else —
# empty, spaces, YAML-special punctuation, reserved words — gets JSON-quoted
# (JSON ⊂ YAML) so the dump stays valid + re-loadable.
_SAFE_BARE = re.compile(r"^[A-Za-z0-9_./:@+-]+$")
# A bare token YAML would re-type: numbers re-load as int/float, these words as
# bool/null. Such strings must be quoted so the dump round-trips without drift.
_NUMERIC = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "none", "~"}


def emit_config_dump(
    settings: Settings,
    provenance: Provenance,
    *,
    config_dir: Path | None,
    stream: IO[str],
) -> Path | None:
    """Startup observability: print the masked provenance dump to `stream`,
    and write the full real-value dump (0600) to `<config_dir>/config.resolved.yaml`
    (cwd when no config dir). Returns the file path written, or None if the
    write failed.

    Best-effort: a failed write must never break startup — it's logged to the
    same stream and `None` is returned. The masked dump always prints first."""
    stream.write("[config] resolved (← source shown, secrets masked):\n")
    stream.write(render(settings, provenance, reveal_secrets=False))

    target = (config_dir or Path.cwd()) / RESOLVED_FILENAME
    try:
        target.write_text(render(settings, provenance, reveal_secrets=True), encoding="utf-8")
        target.chmod(0o600)
    except OSError as exc:
        stream.write(f"[config] could not write {target} ({exc}); dump above is stdout-only\n")
        return None
    stream.write(f"[config] full resolved config (real values, 0600) → {target}\n")
    return target


def render(settings: Settings, provenance: Provenance, *, reveal_secrets: bool) -> str:
    """Annotated YAML for the whole resolved tree. One line per leaf with a
    `# ← <source>` comment; secrets masked unless `reveal_secrets`."""
    lines: list[str] = []
    _render_node(dataclasses.asdict(settings), "", "", provenance, reveal_secrets, lines)
    return "\n".join(lines) + "\n"


def _render_node(
    node: Any, path: str, indent: str, prov: Provenance, reveal: bool, lines: list[str]
) -> None:
    """Emit `node` (always a non-empty dict or list — `render` feeds asdict and
    only recurses on branches). Dict entries render as `key: …`, list entries as
    `- …`."""
    if isinstance(node, dict):
        entries = [(_join(path, str(k)), v, f"{k}:") for k, v in node.items()]
    else:
        entries = [(f"{path}[{i}]", v, "-") for i, v in enumerate(node)]
    for child, value, label in entries:
        if _is_branch(value):
            lines.append(f"{indent}{label}")
            _render_node(value, child, indent + "  ", prov, reveal, lines)
        else:
            src = prov.get(child, _DEFAULT)
            lines.append(f"{indent}{label} {_value(child, value, src, reveal)}  # ← {src.kind}")


_DEFAULT = Source("default")


def _is_branch(value: Any) -> bool:
    """A non-empty dict/list recurses; an empty one is a leaf (`parsers: []`)."""
    return bool(value) and isinstance(value, dict | list)


def _value(path: str, value: Any, src: Source, reveal: bool) -> str:
    if _is_secret(path) and not reveal:
        return _mask(value, src)
    return _scalar(value)


def _is_secret(path: str) -> bool:
    """Leaves that must be masked on stdout. By leaf name (`api_key`, `pg_dsn`,
    any `*_token`) plus the rabbitmq URL (may embed amqp credentials).
    `base_url` is intentionally excluded."""
    leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
    if leaf in ("api_key", "pg_dsn") or leaf.endswith("_token"):
        return True
    return path.endswith("rabbitmq.url")


def _mask(value: Any, src: Source) -> str:
    """`*** unset ***` for empty, else `*** set (N chars) ***` (+ ` via ${VAR}`
    when the value was env-sourced) — proves set/unset without leaking."""
    if not value:
        return "*** unset ***"
    n = len(value) if isinstance(value, str) else len(str(value))
    via = f" via {src.ref}" if src.kind == SOURCE_ENV and src.ref else ""
    return f"*** set ({n} chars){via} ***"


def _scalar(value: Any) -> str:
    """A single YAML scalar — bare when safe, JSON-quoted otherwise."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        bare = (
            value
            and _SAFE_BARE.match(value)
            and value.lower() not in _RESERVED
            and not _NUMERIC.match(value)
            # A trailing colon (e.g. `specstar:`) reads as a nested mapping key
            # when emitted bare — `_SAFE_BARE` allows ':' mid-token (urls, model
            # tags like qwen3:14b) but a trailing one must be quoted to round-trip.
            and not value.endswith(":")
        )
        return value if bare else json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return json.dumps(
        value, ensure_ascii=False, default=str
    )  # pragma: no cover — asdict leaves are scalar/dict/list only


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key

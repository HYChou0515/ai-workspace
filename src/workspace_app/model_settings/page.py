"""What the settings page shows and checks, apart from the routes
(docs/plan-model-settings.md P7).

- `defaults_document` — the deploy defaults in the shape the page edits:
  config.yaml's own tree (`agents.<purpose>`, not the loaded `sub_agents`),
  only the values the page may change (M18), secrets masked by the boot
  dump's rule (M7); every App's app.json and every profile as shipped.
- `key_path_problem` — whether a path may hold a key (M6).
- `try_connection` — one minimal call to a preset's first endpoint (M8).
"""

from __future__ import annotations

import dataclasses
import time
import types
import typing
from pathlib import Path
from typing import Any

from msgspec import Struct

from ..apps.catalog import discover_app_slugs
from ..apps.manifest import read_app_manifest_raw
from ..apps.profiles import list_profiles, read_profile_raw
from ..config.dump import is_secret
from ..config.schema import Settings
from .effective import LIVE_CONFIG, SET, UNSET, key_destination_problems, key_segments


def config_tree(settings: Settings) -> dict[str, Any]:
    """`settings` as config.yaml spells it: the usage lists back under
    `agents.<purpose>` (the loader packs them into `sub_agents`)."""
    tree = dataclasses.asdict(settings)
    agents = tree.get("agents") or {}
    sub_agents = agents.pop("sub_agents", None) or {}
    agents.update(sub_agents)
    return tree


def _at(tree: dict[str, Any], path: str) -> tuple[bool, Any]:
    node: Any = tree
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def live_config(tree: dict[str, Any]) -> dict[str, Any]:
    """Only the values the page may change (M18)."""
    out: dict[str, Any] = {}
    for path in LIVE_CONFIG:
        found, value = _at(tree, path)
        if not found:
            continue
        *parents, leaf = path.split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def mask(node: Any, path: str = "") -> Any:
    """`node` with every secret leaf replaced by SET / UNSET."""
    if isinstance(node, dict):
        return {k: mask(v, f"{path}.{k}" if path else str(k)) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [mask(v, f"{path}[{i}]") for i, v in enumerate(node)]
    if path and is_secret(path):
        return SET if node else UNSET
    return node


def defaults_document(settings: Settings) -> dict[str, Any]:
    slugs = discover_app_slugs()
    return {
        "config": mask(live_config(config_tree(settings))),
        "apps": {slug: read_app_manifest_raw(slug) for slug in slugs},
        "profiles": {
            slug: {name: read_profile_raw(slug, name) for name in list_profiles(slug)}
            for slug in slugs
        },
    }


def _is_text_setting(parts: list[str]) -> bool:
    """Whether `parts` (below `config`) name a text setting in the schema —
    walking the settings dataclasses, a `dict[str, V]` taking any one key, an
    optional value its inner type. A key anywhere else would be refused by the
    loader, whose message quotes the value back (review r2)."""
    kind: Any = Settings
    for part in parts:
        kind = _strip_optional(kind)
        if dataclasses.is_dataclass(kind):
            hints = typing.get_type_hints(kind)
            if part not in hints:
                return False
            kind = hints[part]
        elif typing.get_origin(kind) is dict:
            kind = typing.get_args(kind)[1]
        else:
            return False
    return _strip_optional(kind) is str


def _strip_optional(kind: Any) -> Any:
    args = [a for a in typing.get_args(kind) if a is not type(None)]
    is_union = typing.get_origin(kind) in (typing.Union, types.UnionType)
    return args[0] if is_union and len(args) == 1 else kind


def key_lands(settings: Settings, path: str, value: str) -> bool:
    """Whether a key at `path` reached `settings` — its preset or role exists.
    The loader drops a key whose owner is not there (a hidden preset, a role
    turned off); at the moment a key is SET that is a typo, and a key that
    does nothing must not be taken in silence (final review)."""
    node: Any = settings
    for part in key_segments(path)[1:]:
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            node = getattr(node, part, None)
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return False
    return node == value


def key_path_problem(path: str) -> str | None:
    """Why `path` cannot hold a key, or None. A key is a secret (M7's rule)
    inside the part of config the page edits (M18), at a text setting."""
    if not path.startswith("config."):
        return f"{path}: a key lives in the config part (config.…)"
    inner = path.removeprefix("config.")
    if not is_secret(inner):
        return f"{path}: not a secret — set it in the text, not as a key"
    if not any(inner == live or inner.startswith(live + ".") for live in LIVE_CONFIG):
        return f"{path}: read only when the server starts — set it in config.yaml"
    parts = key_segments(path)[1:]
    # config.yaml's `agents.<purpose>` lists are `sub_agents` once loaded; a
    # key inside a list is not a text setting either way.
    if not _is_text_setting(parts):
        return f"{path}: no such text setting — a key is set at a preset's llm.api_key or env"
    return None


class Connection(Struct, frozen=True):
    """One test call's outcome. `error` never carries the key."""

    ok: bool
    model: str = ""
    ms: int = 0
    reply: str = ""
    error: str = ""


#: The prompt a test call sends: short, and answerable by any chat model.
PING = "Reply with the single word OK."


def try_connection(
    settings: Settings,
    preset: str,
    *,
    deploy: Settings | None = None,
    page_keys: set[str] | frozenset[str] = frozenset(),
    config_dir: Path | None,
    timeout_s: float = 30.0,
) -> Connection:
    """Call `preset`'s first endpoint once with `settings` (the draft, keys
    included). Never part of Apply (M8): a provider that is down must not stop
    a rollback."""
    from ..config.schema import RetrievalLlmRef
    from ..factories import resolve_llm_chain
    from ..kb.llm import LitellmLlm

    if preset not in settings.agents.presets:
        return Connection(ok=False, error=f"no preset named {preset!r}")
    if deploy is not None:
        # The deploy's key goes only where config.yaml sends it (final review).
        leaks = key_destination_problems(
            settings, deploy, set(page_keys), presets=[preset], config_dir=config_dir
        )
        if leaks:
            return Connection(ok=False, error=leaks[0])
    chain = resolve_llm_chain(settings, RetrievalLlmRef(preset=preset))
    if not chain:
        return Connection(ok=False, error=f"preset {preset!r} resolves to no endpoint")
    head = chain[0]
    started = time.monotonic()
    try:
        reply = LitellmLlm(
            head.model, base_url=head.base_url, api_key=head.api_key, timeout=timeout_s
        ).collect(PING)
    except Exception as exc:  # any provider failure is the answer this button gives
        error = str(exc)
        if head.api_key:
            error = error.replace(head.api_key, SET)
        return Connection(ok=False, model=head.model, error=error[:500])
    return Connection(
        ok=True,
        model=head.model,
        ms=int((time.monotonic() - started) * 1000),
        reply=reply.strip()[:200],
    )

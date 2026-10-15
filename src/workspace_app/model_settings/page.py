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
import re
import time
import types
import typing
from pathlib import Path
from typing import Any

from msgspec import Struct

from ..apps.catalog import discover_app_slugs
from ..apps.manifest import read_app_manifest_raw
from ..apps.profiles import list_profiles, read_profile_raw
from ..config.dump import config_tree, is_secret_at
from ..config.schema import Settings
from .effective import (
    LIVE_CONFIG,
    ROLES,
    SET,
    UNSET,
    key_destination_problems,
    key_segments,
)


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


def mask(node: Any, parts: tuple[str | int, ...] = ()) -> Any:
    """`node` with every secret leaf replaced by SET / UNSET — decided on the
    walked `parts`, since a name may hold a dot (final review 9)."""
    if isinstance(node, dict):
        return {k: mask(v, (*parts, str(k))) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [mask(v, (*parts, i)) for i, v in enumerate(node)]
    if parts and is_secret_at(parts):
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


class KeySlot(Struct, frozen=True):
    """One place a key can go: `owner` is a preset or role name ("" for the
    default `llm`), `kind` is "api_key" or "env" (then `name` is the variable)."""

    path: str
    owner: str
    kind: str
    name: str = ""


def key_slots(settings: Settings) -> list[KeySlot]:
    """Every place a key can sit in `settings` — so the page offers them to
    pick from, and nobody types a path (M22; NN/g heuristic #6, recognition
    rather than recall). Each is one the key route accepts."""
    slots = [KeySlot(path="config.llm.api_key", owner="", kind="api_key")]
    for name, preset in sorted(settings.agents.presets.items()):
        base = f"config.agents.presets.{name}"
        slots.append(KeySlot(path=f"{base}.llm.api_key", owner=name, kind="api_key"))
        for var in sorted(preset.env or {}):
            slots.append(KeySlot(path=f"{base}.env.{var}", owner=name, kind="env", name=var))
    for role in ROLES:
        # The role's own reference, not the fallback `ROLES` resolves to: a key
        # lands only where the reference is written (`key_lands`).
        node: Any = settings
        for part in role.split("."):
            node = getattr(node, part, None)
        if node is not None:
            slots.append(KeySlot(path=f"config.{role}.llm.api_key", owner=role, kind="api_key"))
    return [slot for slot in slots if key_path_problem(slot.path) is None]


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
    inside the part of config the page edits (M18), at a text setting.

    Whether a key can sit there at all is asked first, then whether the page
    edits it, then whether it is a secret: each answer is advice the next step
    accepts. Given for a typo, "set it in the text" sent a real key into the
    draft, history and diffs (final review 6); given for a boot-only setting it
    sent the person to a text that refuses it (final review 7)."""
    if not path.startswith("config."):
        return f"{path}: a key lives in the config part (config.…)"
    inner = path.removeprefix("config.")
    parts = key_segments(path)[1:]
    # config.yaml's `agents.<purpose>` lists are `sub_agents` once loaded; a
    # key inside a list is not a text setting either way.
    if not _is_text_setting(parts):
        return (
            f"{path}: not a text setting a key can sit at — a key goes at a preset's "
            "or role's llm.api_key, or a preset's env.<NAME>"
        )
    # Boot-only before "in the text": the text refuses a boot-only setting too
    # (final review 7 — `server.host` was sent to the text, then refused there).
    if not any(inner == live or inner.startswith(live + ".") for live in LIVE_CONFIG):
        return f"{path}: read only when the server starts — set it in config.yaml"
    if not is_secret_at(parts):
        return f"{path}: not a secret — set it in the text, not as a key"
    return None


class Connection(Struct, frozen=True):
    """One test call's outcome: `ms` is how long the model took to start
    answering. `error` never carries the key."""

    ok: bool
    model: str = ""
    ms: int = 0
    error: str = ""


#: The prompt a test call sends: short, and answerable by any chat model.
PING = "Reply with the single word OK."


def _scrub(text: str, key: str | None) -> str:
    """`text` with the key masked wherever it stands as a whole token — any
    length, since a provider error quotes the key it was sent (a short one
    too). Not inside a longer word: replacing a key "e" everywhere mangled the
    error and showed which letters the key held (final review 12)."""
    if not key:
        return text
    return re.sub(rf"(?<![\w-]){re.escape(key)}(?![\w-])", SET, text)


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
    included), and stop at its first token. Never part of Apply (M8): a
    provider that is down must not stop a rollback.

    The first token — thinking included — already shows the endpoint answers,
    the key is taken and the model exists (M24): waiting for the whole reply
    cost a thinking model on CPU ~35 s, where its first token came in ~1-3 s."""
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
    chunks = None
    try:
        chunks = LitellmLlm(
            head.model, base_url=head.base_url, api_key=head.api_key, timeout=timeout_s
        ).stream(PING)
        answered = any(text for text, _reasoning in chunks)
    except Exception as exc:  # any provider failure is the answer this button gives
        error = _scrub(str(exc), head.api_key)
        return Connection(ok=False, model=head.model, error=error[:500])
    finally:
        # Hang up: the provider stops generating the rest of the reply.
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
    if not answered:
        return Connection(ok=False, model=head.model, error="the model answered nothing")
    return Connection(ok=True, model=head.model, ms=int((time.monotonic() - started) * 1000))

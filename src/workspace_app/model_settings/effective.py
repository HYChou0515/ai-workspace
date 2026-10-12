"""The settings everything runs with (docs/plan-model-settings.md P3).

The deploy defaults — config.yaml over the bundled settings, each App's
app.json, each profile's _profile.json — with the page's published patch and
keys over them. The page edits one document:

    {"config":   {...the config.yaml tree...},
     "apps":     {"<slug>": {...app.json...}},
     "profiles": {"<slug>": {"<profile>": {..._profile.json...}}}}

each part a JSON Merge Patch (M11, M12). Every function here takes the document
it is given and reads nothing published, so the page can check a draft and a
consumer can build from the published one with the same code (M13).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import msgspec

from ..apps.catalog import _subset_or_raise, discover_app_slugs, validate_function_coherence
from ..apps.manifest import AppManifest, patched_app
from ..apps.profiles import ProfileManifest, list_profiles, patched_profile
from ..config.dump import is_secret
from ..config.loader import load_with_provenance
from ..config.schema import Settings
from ..workflow.discovery import validate_workflow_profiles
from .store import SHOWN_FROM


def key_segments(path: str) -> list[str]:
    """A key's dotted path as its parts. A preset name may hold a dot
    (`qwen2.5`), so a preset's key is split around the name: what follows it is
    `llm.<leaf>` or `env.<VAR>`, and neither part has a dot."""
    parts = path.split(".")
    head = ["config", "agents", "presets"]
    if parts[:3] == head and len(parts) >= 6 and parts[-2] in ("llm", "env"):
        return [*head, ".".join(parts[3:-2]), *parts[-2:]]
    return parts


def keys_by_parts(keys: Mapping[str, str]) -> list[tuple[list[str], str]]:
    """The config keys as (parts below `config`, value) for the loader, which
    puts each where its setting exists after the merge — never into the patch,
    so history never holds one (M6) and a key cannot create a setting."""
    out: list[tuple[list[str], str]] = []
    for path, value in keys.items():
        parts = key_segments(path)
        if parts[0] == "config" and len(parts) > 1:
            out.append((parts[1:], value))
    return out


def effective_settings(
    document: dict[str, Any],
    *,
    config_path: Path | None,
    env: Mapping[str, str],
    keys: Mapping[str, str] | None = None,
) -> Settings:
    """The typed settings with the document's `config` part and `keys` applied
    — through the loader, so every check a boot runs runs here too (M8)."""
    settings, _ = load_with_provenance(
        config_path=config_path,
        env=env,
        overlay=document.get("config") or None,
        keys=keys_by_parts(keys or {}),
    )
    return settings


def effective_app(slug: str, document: dict[str, Any]) -> AppManifest:
    return patched_app(slug, document)


def effective_profile(slug: str, name: str, document: dict[str, Any]) -> ProfileManifest:
    return patched_profile(slug, name, document)


#: The config values a running process reads again after an Apply (P5/P6):
#: the model choices of M3. Anything else in config.yaml is read once at boot,
#: so the page refuses it rather than store a value that does nothing until a
#: restart.
LIVE_CONFIG: tuple[str, ...] = (
    "llm",
    # presets, subagent_models and every usage list (`kb_chat`, `infer_modules`, …
    # — `agents.<purpose>` in config.yaml, `sub_agents` once loaded)
    "agents",
    "kb.retrieval_llm",
    "kb.vlm_llm",
    "kb.vlm_format_llm",
    "kb.deck_vlm",
    "kb.card_drafter",
    "kb.quality_judge",
    "kb.wiki.llm",
    "health.judge_llm",
    "goal.checker",
)


#: app.json keys read once at boot and held: `resources` becomes the quota
#: ceilings (`quota/limits.py:resolve_discovered_apps`), keyed by `slug`.
BOOT_ONLY_APP_KEYS: tuple[str, ...] = ("resources", "slug")

#: The roles whose being on or off decided, at boot, which features exist —
#: the factories return None for an off role and the feature is not wired.
ROLES: dict[str, Callable[[Settings], Any]] = {
    "kb.retrieval_llm": lambda s: s.kb.retrieval_llm,
    "kb.vlm_llm": lambda s: s.kb.vlm_llm,
    "kb.vlm_format_llm": lambda s: s.kb.vlm_format_llm or s.kb.retrieval_llm,
    "kb.deck_vlm": lambda s: s.kb.deck_vlm or s.kb.vlm_llm,
    "kb.card_drafter": lambda s: s.kb.card_drafter,
    "kb.quality_judge": lambda s: s.kb.quality_judge or s.kb.retrieval_llm,
    "kb.wiki.llm": lambda s: s.kb.wiki.llm,
    "health.judge_llm": lambda s: s.health.judge_llm,
    "goal.checker": lambda s: s.goal.checker or s.kb.retrieval_llm,
}


def _roles_switched(settings: Settings, default: Settings) -> list[str]:
    from ..factories import resolve_llm_chain

    return [
        f"config.{role}: turning a role on or off changes which features the "
        "server wires when it starts — change it in config.yaml (here you can "
        "change which model it uses)"
        for role, ref_of in ROLES.items()
        if bool(resolve_llm_chain(settings, ref_of(settings)))
        != bool(resolve_llm_chain(default, ref_of(default)))
    ]


def secrets_written(document: dict[str, Any]) -> list[str]:
    """Every secret typed into the document's text (M6) — refused wherever the
    text is stored or applied; a secret is set as a key instead."""
    return _secrets_written(document.get("config") or {})


#: How the page shows a secret it will not reveal (`page.mask`). Typed back
#: as a value it would become that setting (review r3).
SET, UNSET = "*** set ***", "*** unset ***"


def _secrets_written(patch: Any, path: str = "") -> list[str]:
    """A secret typed into the text would be stored in the patch — shown in
    the diff, kept in history (M6). It is set as a key instead."""
    if isinstance(patch, dict):
        return [
            problem
            for key, value in patch.items()
            for problem in _secrets_written(value, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(patch, list):
        # review r3: a usage entry takes an `llm.api_key` too
        return [p for i, item in enumerate(patch) for p in _secrets_written(item, f"{path}[{i}]")]
    if patch in (SET, UNSET):
        return [
            f"config.{path}: that is how the defaults show a secret, not a value — "
            "leave it out, or set the secret as a key"
        ]
    if patch is not None and path and is_secret(path):
        return [f"config.{path}: a secret is set as a key on this page, not in the text"]
    return []


def _nulls(patch: Any, path: str) -> list[str]:
    if patch is None:
        return [
            f"{path}: null hides a preset and means nothing here; to return a "
            "value to its default, remove it from the patch"
        ]
    if isinstance(patch, dict):
        return [p for key, value in patch.items() for p in _nulls(value, f"{path}.{key}")]
    return []


_NO_SUCH_APP = "no such App — an App is a folder shipped with the server"


PARTS = ("config", "apps", "profiles")


def _env_markers(node: Any, path: str = "") -> list[str]:
    """Every `${…}` in the document. The text is taken literally (the loader
    never expands it — that read the server's environment back to the typist,
    final review); one written expecting expansion is named, not stored as a
    value that would reach a provider as the literal string."""
    if isinstance(node, dict):
        return [
            p for k, v in node.items() for p in _env_markers(v, f"{path}.{k}" if path else str(k))
        ]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _env_markers(v, f"{path}[{i}]")]
    if isinstance(node, str) and "${" in node:
        return [
            f"{path}: this page does not read the server's environment (${{…}}) — "
            "write the value itself, or set a secret as a key"
        ]
    return []


def _shape_problems(document: dict[str, Any]) -> list[str]:
    """The document's own shape, checked before anything reads it (review r2: a
    part that was not an object crashed the check after the draft was stored,
    and a null part was taken as empty — stored, shown, doing nothing)."""
    found = [
        f"{key}: not a part of the settings (one of {', '.join(PARTS)})"
        for key in document
        if key not in PARTS
    ]
    levels = {"config": 0, "apps": 1, "profiles": 2}
    for part, depth in levels.items():
        if part in document:
            found += _objects(document[part], part, depth)
    return found


def _objects(value: Any, path: str, depth: int) -> list[str]:
    """`value` and `depth` levels below it are objects {…}."""
    if not isinstance(value, dict):
        return [f"{path}: must be an object {{…}}, got {type(value).__name__}"]
    if depth == 0:
        return []
    return [p for key, child in value.items() for p in _objects(child, f"{path}.{key}", depth - 1)]


def _apps_part_problems(document: dict[str, Any]) -> list[str]:
    """What the apps / profiles parts cannot mean: an App or a profile that is
    not shipped (it is a folder beside the server — a patch can change one, not
    create one, M18), and a null (M19)."""
    slugs = set(discover_app_slugs())
    found: list[str] = []
    for slug, patch in (document.get("apps") or {}).items():
        if slug not in slugs:
            found.append(f"apps.{slug}: {_NO_SUCH_APP}")
        else:
            found += _nulls(patch, f"apps.{slug}")
    for slug, profiles in (document.get("profiles") or {}).items():
        if slug not in slugs:
            found.append(f"profiles.{slug}: {_NO_SUCH_APP}")
            continue
        shipped = set(list_profiles(slug))
        for name, patch in (profiles or {}).items():
            if name not in shipped:
                found.append(
                    f"profiles.{slug}.{name}: no such profile — a profile is a folder "
                    f"in apps/{slug}/profiles; add it there"
                )
            else:
                found += _nulls(patch, f"profiles.{slug}.{name}")
    return found


def _config_nulls(patch: Any, parts: tuple[str, ...] = ()) -> list[str]:
    """Every null in the config part that does not hide a preset (M12, M19) —
    walked through the whole part, live subtrees included: the scope walk stops
    at a live key such as `agents` and would never see one inside it."""
    found: list[str] = []
    for key, value in patch.items() if isinstance(patch, dict) else ():
        here = (*parts, key)
        if value is None:
            # by parts, not by counting dots: a preset name may hold one (`qwen2.5`)
            if not (here[:2] == ("agents", "presets") and len(here) == 3):
                found.append(
                    f"config.{'.'.join(here)}: null only hides a preset; to return it "
                    "to its config.yaml value, remove it from the patch"
                )
        else:
            found += _config_nulls(value, here)
    return found


def _outside_live(patch: Any, path: str = "") -> list[str]:
    """Every path in a config patch that is not live."""
    found: list[str] = []
    for key, value in patch.items() if isinstance(patch, dict) else ():
        here = f"{path}.{key}" if path else key
        if value is None:
            continue  # `_config_nulls` judges every null, inside live parts too
        if any(here == live or here.startswith(live + ".") for live in LIVE_CONFIG):
            continue
        elif isinstance(value, dict) and any(live.startswith(here + ".") for live in LIVE_CONFIG):
            found += _outside_live(value, here)
        else:
            found.append(
                f"config.{here}: read only when the server starts, so it is not "
                "changed here — change it in config.yaml"
            )
    return found


def redact(text: str, keys: Mapping[str, str] | None) -> str:
    """`text` with each long key's value replaced — for anything a route hands
    back, since a refusal may quote a value (`got {x!r}`) (M6, review r2).

    Only a value of `SHOWN_FROM` characters or more: scrubbing a short one out
    of every message shredded them (a key "e" took every letter e) and told a
    guesser which strings it held (review r3). A key path is a text setting
    (`page.key_path_problem`), so the loader has no shape to refuse and quote."""
    values: list[str] = (
        [] if keys is None else [str(v) for v in keys.values() if len(str(v)) >= SHOWN_FROM]
    )
    values.sort(key=len, reverse=True)  # a longer key first, if one holds another
    out = text
    for value in values:
        if value:
            out = out.replace(value, "*** key ***")
    return out


def problems(
    document: dict[str, Any],
    *,
    config_path: Path | None,
    env: Mapping[str, str],
    keys: Mapping[str, str] | None = None,
) -> list[str]:
    """What stops `document` from running — empty when it can. Each is scrubbed
    of every key of `SHOWN_FROM` characters or more (`redact`); a shorter one is
    not scrubbed, and cannot reach one by the loader, since a key sits only at a
    text setting (`page.key_path_problem`)."""
    return [
        redact(problem, keys)
        for problem in _problems(document, config_path=config_path, env=env, keys=keys)
    ]


def _problems(
    document: dict[str, Any],
    *,
    config_path: Path | None,
    env: Mapping[str, str],
    keys: Mapping[str, str] | None = None,
) -> list[str]:
    """What stops `document` from running — empty when it can. Apply refuses
    on any (M8). Structure only: a model name that exists nowhere passes, which
    is what the test-connection button is for.

    The checks are the platform's own — the loader's, `validate_function_coherence`,
    the profile ⊂ picker subset check — plus one boot never ran: every preset a
    picker or profile names exists, which today is found only when a turn
    resolves it.

    `keys` are checked with the document as `LiveSettings` builds it, but only
    the document's own text is checked for a secret typed into it."""
    shape = _shape_problems(document) + _env_markers(document)
    if shape:
        return shape
    written = secrets_written(document) + _apps_part_problems(document)
    outside = (
        written
        + _config_nulls(document.get("config") or {})
        + _outside_live(document.get("config") or {})
    )
    if outside:
        return outside
    try:
        settings = effective_settings(document, config_path=config_path, env=env, keys=keys)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return [f"config: {exc}"]
    from ..factories import get_infer_modules_run_config

    default = effective_settings({}, config_path=config_path, env=env)
    switched = _roles_switched(settings, default)
    if switched:
        return switched
    if get_infer_modules_run_config(settings) != get_infer_modules_run_config(default):
        return [
            "config.agents.infer_modules: enhancements / reasoning_effort / "
            "parallelism / collection are read only when the server starts — change "
            "them in config.yaml"
        ]
    presets = set(settings.agents.presets)
    found: list[str] = []
    for slug in discover_app_slugs():
        app_patch = (document.get("apps") or {}).get(slug) or {}
        found += [
            f"apps.{slug}.{key}: read only when the server starts — change it in "
            f"apps/{slug}/app.json"
            for key in BOOT_ONLY_APP_KEYS
            if key in app_patch
        ]
        try:
            app = effective_app(slug, document)
            validate_function_coherence(app)
        except (msgspec.ValidationError, ValueError) as exc:
            found.append(f"apps.{slug}: {exc}")
            continue
        picker = [entry.preset for entry in app.agent.picker]
        found += [
            f"apps.{slug}.agent.picker: preset {name!r} is not in agents.presets"
            for name in picker
            if name not in presets
        ]
        try:
            validate_workflow_profiles(
                slug,
                profile_of=lambda s, n: effective_profile(s, n, document),
                profiles=_profiles(slug, document),
            )
        except Exception as exc:  # whatever boot would refuse with, named
            found.append(f"profiles.{slug}: {exc}")
        for profile in _profiles(slug, document):
            where = f"profiles.{slug}.{profile}"
            try:
                prof = effective_profile(slug, profile, document)
            except msgspec.ValidationError as exc:
                found.append(f"{where}: {exc}")
                continue
            if prof.presets is not msgspec.UNSET:
                try:
                    _subset_or_raise(
                        prof.presets, picker, kind="presets", app=slug, profile=profile
                    )
                except ValueError as exc:
                    found.append(f"{where}: {exc}")
            if prof.default_preset and prof.default_preset not in presets:
                found.append(
                    f"{where}.default_preset: preset {prof.default_preset!r} "
                    "is not in agents.presets"
                )
    return found


def _profiles(slug: str, document: dict[str, Any]) -> list[str]:
    """The App's shipped profiles, and any the document names."""
    named = ((document.get("profiles") or {}).get(slug) or {}).keys()
    return sorted(set(list_profiles(slug)) | set(named))

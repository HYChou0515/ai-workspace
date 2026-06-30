"""Top-level config loader — pipeline orchestrator.

Pipeline (Q1 hard cut + Q2 interpolation-only + Q7 layered defaults):

  1. resolve config.yaml path (explicit / ``WORKSPACE_APP_CONFIG`` env /
     ``./config.yaml`` if present).
  2. parse YAML (missing file → empty dict; YAML errors raise).
  3. walk the parsed tree; for every string value run ``expand_env(...)``
     against ``env`` so ``${FOO}``-templates become live values.
  4. layered merge: ``asdict(Settings())`` (bundled) ◇ operator dict.
  5. strict validation — unknown keys, broken preset references, missing
     required preset fields — all raise here with the offending path.
  6. construct typed ``Settings(...)`` from the merged dict.

Single entry point: ``load(config_path=None, env=None)``.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .interpolate import expand_env, has_env_reference
from .merge import merge_layered
from .schema import (
    AgentsSettings,
    ChunkerSettings,
    CodeEmbedderSettings,
    EmbedderSettings,
    EnhancementBool,
    EnhancementInt,
    EnhancementSettings,
    ExecSettings,
    FailoverSettings,
    FilestoreSettings,
    GitSettings,
    HealthSettings,
    HistorySettings,
    HttpSandboxSettings,
    KbSettings,
    LlmLogSettings,
    LlmSettings,
    MessageQueueSettings,
    ObservabilitySettings,
    Preset,
    PresetLlmSettings,
    RabbitmqSettings,
    ReadFileSettings,
    RetrievalLlmRef,
    RetrievalSettings,
    RunnerSettings,
    SandboxHostSettings,
    SandboxIsolationSettings,
    SandboxSettings,
    ServerSettings,
    Settings,
    ToolsSettings,
    WikiSettings,
)

# Per-leaf provenance: a dotted path into `asdict(Settings)` → where the
# value came from. The three kinds answer the operator's question "did I
# set this, or did it default?".
SOURCE_CONFIG = "config.yaml"  # operator wrote this leaf in config.yaml
SOURCE_ENV = "env"  # operator wrote it, value came from a ${VAR} marker
SOURCE_DEFAULT = "default"  # operator did NOT write it — bundled default applies


@dataclasses.dataclass(frozen=True)
class Source:
    """Where one resolved leaf came from. `ref` carries the raw `${VAR}`
    template (only when `kind == env`) so the dump can tell the operator
    WHICH env var feeds a masked secret — empty otherwise."""

    kind: str  # SOURCE_CONFIG | SOURCE_ENV | SOURCE_DEFAULT
    ref: str = ""


Provenance = dict[str, Source]


def load(
    *,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Build a `Settings` from operator's `config.yaml` layered on top
    of bundled defaults. See module docstring for the pipeline."""
    settings, _ = load_with_provenance(config_path=config_path, env=env)
    return settings


def load_with_provenance(
    *,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Settings, Provenance]:
    """`load` plus a per-leaf provenance map (observability feature A): for
    every dotted path in `asdict(settings)`, whether the value came from the
    operator's `config.yaml`, a `${VAR}` env marker, or the bundled default.

    Provenance is derived from the RAW (pre-interpolation) parsed YAML — the
    set of leaves the operator actually wrote — so it answers "what did I set"
    independently of whether a written value happens to equal the default."""
    e = os.environ if env is None else env

    # 1 — resolve config.yaml path
    path = _resolve_config_path(config_path, e)

    # 2 — parse YAML. Keep the raw tree (with ${VAR} intact) for provenance;
    # interpolation below works on a fresh copy so `raw_yaml` is untouched.
    raw_yaml = _load_yaml(path) if path is not None else {}

    # 3 — env interpolation on every string value
    yaml_d = _walk_strings(raw_yaml, lambda s: expand_env(s, e))

    # 4 — layered merge over bundled defaults. The bundled tree from
    # `asdict(Settings())` holds usage lists nested under
    # `agents.sub_agents.<purpose>`, but operators write them flat as
    # `agents.<purpose>` (B-flat schema). Flatten the bundled side so
    # the merge sees both at the same path; pack back into `sub_agents`
    # after the merge so `_settings_from_dict` reads the typed shape.
    bundled = dataclasses.asdict(Settings())
    _flatten_bundled_sub_agents(bundled)
    merged = merge_layered(bundled, yaml_d)

    # 5 — strict validation
    _validate(merged, source=str(path) if path else "<bundled defaults>")

    _pack_merged_sub_agents(merged)

    # 6 — construct typed Settings
    settings = _settings_from_dict(merged)

    # 7 — derive provenance from the raw operator YAML + the resolved tree
    provenance = _build_provenance(raw_yaml, settings)
    return settings, provenance


# ─── provenance ─────────────────────────────────────────────────────────


def _build_provenance(raw_yaml: dict[str, Any], settings: Settings) -> Provenance:
    """Assign every leaf in `asdict(settings)` a source. A leaf the operator
    wrote (matched by dotted path) takes its `config.yaml`/`env` source;
    everything else is `default`."""
    op_sources: dict[str, Source] = {}
    _collect_operator_sources(raw_yaml, "", op_sources)
    op_sources = _remap_agents_paths(op_sources)
    prov: Provenance = {}
    _assign_settings_sources(dataclasses.asdict(settings), "", op_sources, prov)
    return prov


def _remap_agents_paths(op_sources: dict[str, Source]) -> dict[str, Source]:
    """Operators write sub-agent purposes flat (`agents.workspace_chat`), but
    `_pack_merged_sub_agents` nests them under `agents.sub_agents.<purpose>` in
    the resolved tree. Rewrite those operator paths so they line up with
    `asdict(settings)`. `agents.presets.*` is NOT a purpose — left untouched."""
    prefix = "agents."
    out: dict[str, Source] = {}
    for path, src in op_sources.items():
        if path.startswith(prefix):
            rest = path[len(prefix) :]
            purpose = rest.split(".", 1)[0].split("[", 1)[0]
            if purpose != "presets":
                path = f"agents.sub_agents.{rest}"
        out[path] = src
    return out


def _collect_operator_sources(raw: Any, prefix: str, out: dict[str, Source]) -> None:
    """Walk the raw (pre-interpolation) operator YAML; record each leaf's
    dotted path → a `Source`: `env` (its string held a ${VAR} marker, raw
    template kept in `ref`) or `config.yaml` (a fixed literal)."""
    if isinstance(raw, dict):
        for k, v in raw.items():
            _collect_operator_sources(v, _join(prefix, str(k)), out)
    elif isinstance(raw, list):
        for i, v in enumerate(raw):
            _collect_operator_sources(v, f"{prefix}[{i}]", out)
    elif isinstance(raw, str) and has_env_reference(raw):
        out[prefix] = Source(SOURCE_ENV, raw)
    else:
        out[prefix] = Source(SOURCE_CONFIG)


def _assign_settings_sources(
    node: Any, prefix: str, op_sources: dict[str, Source], out: Provenance
) -> None:
    """Walk `asdict(settings)`; each leaf path takes the operator source if
    the operator wrote that exact path, else `default`. Empty dict/list nodes
    are themselves leaves (e.g. `parsers: []`)."""
    if isinstance(node, dict) and node:
        for k, v in node.items():
            _assign_settings_sources(v, _join(prefix, str(k)), op_sources, out)
    elif isinstance(node, list) and node:
        for i, v in enumerate(node):
            _assign_settings_sources(v, f"{prefix}[{i}]", op_sources, out)
    else:
        out[prefix] = op_sources.get(prefix, Source(SOURCE_DEFAULT))


def _flatten_bundled_sub_agents(bundled: dict[str, Any]) -> None:
    """Move `bundled.agents.sub_agents.<purpose>` keys up to
    `bundled.agents.<purpose>` so the merge layer's path-matching sees
    operator's flat YAML as overriding the bundled list."""
    agents = bundled.get("agents")
    if not isinstance(agents, dict):
        return
    sub_agents = agents.pop("sub_agents", None)
    if isinstance(sub_agents, dict):
        agents.update(sub_agents)


def _pack_merged_sub_agents(merged: dict[str, Any]) -> None:
    """Inverse of `_flatten_bundled_sub_agents`. After the merged dict
    is validated (every non-`presets` key under `agents` is a usage
    list), pack them back into `agents.sub_agents` for
    `_settings_from_dict` to consume."""
    agents = merged.get("agents")
    if not isinstance(agents, dict):
        return
    presets = agents.get("presets", {})
    sub_agents = {k: v for k, v in agents.items() if k != "presets"}
    agents.clear()
    agents["presets"] = presets
    agents["sub_agents"] = sub_agents


# ─── helpers ────────────────────────────────────────────────────────────


def _resolve_config_path(explicit: Path | None, env: Mapping[str, str]) -> Path | None:
    """Three-step lookup: explicit arg > `WORKSPACE_APP_CONFIG` env >
    `./config.yaml` if present. None if none of those resolve to a
    real file — `load` treats that as "no operator config, all bundled
    defaults"."""
    if explicit is not None:
        return explicit if explicit.is_file() else None
    env_cfg = env.get("WORKSPACE_APP_CONFIG")
    if env_cfg:
        p = Path(env_cfg)
        return p if p.is_file() else None
    cwd_cfg = Path("./config.yaml")
    return cwd_cfg if cwd_cfg.is_file() else None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read + parse a YAML config file. Missing → {}. Parse error /
    non-mapping root raises ValueError naming the path."""
    if not path.is_file():
        return {}
    import yaml

    try:
        loaded = yaml.safe_load(path.read_text("utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"config file {path}: YAML parse error — {e}") from e
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"config file {path}: expected a mapping at the root, got {type(loaded).__name__}"
        )
    return loaded


def _walk_strings(node: Any, fn) -> Any:
    """Walk a YAML-parsed structure (dict / list / scalar) and apply
    `fn` to every string leaf. Returns a fresh structure; the input
    is not mutated. Non-string leaves (int / float / bool / None) pass
    through verbatim."""
    if isinstance(node, dict):
        return {k: _walk_strings(v, fn) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk_strings(v, fn) for v in node]
    if isinstance(node, str):
        return fn(node)
    return node


# ─── validation ─────────────────────────────────────────────────────────


def _validate(merged: dict[str, Any], *, source: str) -> None:
    """Strict structural checks. Raises ValueError on the first
    problem; the message names the field path + the source file."""
    _check_unknown_keys(merged, _TOP_SCHEMA, prefix="", source=source)
    _check_preset_references(merged, source=source)
    _check_preset_fallbacks(merged, source=source)
    _check_preset_required_fields(merged, source=source)
    _check_retrieval_llm_reference(merged, source=source)
    _check_max_searches(merged, source=source)


def _check_max_searches(merged: dict[str, Any], *, source: str) -> None:
    """Issue #195: `kb.max_searches_per_turn` is `null` (no cap) or a positive
    integer. A zero/negative cap would disable kb_search entirely, which is
    what `null`… is NOT for — use `null` to lift the cap. Reject it loudly so
    an operator typo doesn't silently mute the knowledge base."""
    value = merged.get("kb", {}).get("max_searches_per_turn")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
        raise ValueError(
            f"config {source}: kb.max_searches_per_turn must be null or a positive "
            f"integer, got {value!r}"
        )
    # Issue #334: the ceiling bounds a per-message pick. When the operator sets
    # it, it must be a positive int (a 0/negative/non-int would break clamping).
    # Absent ⇒ the bundled default (10) applies, so only validate a present value
    # — same leniency as `max_searches_per_turn` above.
    ceiling = merged.get("kb", {}).get("max_searches_ceiling")
    if ceiling is not None and (
        not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 1
    ):
        raise ValueError(
            f"config {source}: kb.max_searches_ceiling must be a positive integer, got {ceiling!r}"
        )


# The shape of the legal key tree. A `dict` value here means "this key
# is a mapping; recurse into the sub-dict for its allowed keys". A
# `set` means "this key is a mapping whose keys are operator-chosen,
# but every value must match this sub-dict shape". `None` means "this
# key is a leaf scalar/list — no further key checks".
#
# We can't fully derive this from the dataclass tree because:
#   - `agents.presets` is a map keyed by operator-chosen names (typed
#     values though — Preset's fields are the allowed keys).
#   - `agents.workspace_chat[]` / `agents.kb_chat` are usage dicts with
#     `preset` + optional Preset-field overrides.
# So we encode it explicitly.

_PRESET_FIELDS = {f.name for f in dataclasses.fields(Preset)}
_PRESET_LLM_FIELDS = {f.name for f in dataclasses.fields(PresetLlmSettings)}
# `reasoning_effort` / `enhancements` / `parallelism` / `collection` (#66):
# infer_modules entries carry the per-step classifier's KB-query depth +
# effort + fan-out + which collection to search. Allowed on any usage entry
# (other purposes simply ignore them).
_USAGE_FIELDS = _PRESET_FIELDS | {
    "preset",
    "name",
    "reasoning_effort",
    "enhancements",
    "parallelism",
    "collection",
}
_ENHANCEMENT_KEYS = {"expand", "hyde", "rerank"}


def _dataclass_keys(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


_TOP_SCHEMA: dict[str, Any] = {
    "server": _dataclass_keys(ServerSettings),
    "sandbox": {
        **{k: None for k in _dataclass_keys(SandboxSettings) if k not in ("http", "isolation")},
        "http": _dataclass_keys(HttpSandboxSettings),
        "isolation": _dataclass_keys(SandboxIsolationSettings),
    },
    "sandbox_host": _dataclass_keys(SandboxHostSettings),
    "tools": _dataclass_keys(ToolsSettings),
    "filestore": _dataclass_keys(FilestoreSettings),
    "runner": _dataclass_keys(RunnerSettings),
    "llm": _dataclass_keys(LlmSettings),
    "read_file": _dataclass_keys(ReadFileSettings),
    "exec": _dataclass_keys(ExecSettings),
    "history": _dataclass_keys(HistorySettings),
    "kb": {
        "embedder": _dataclass_keys(EmbedderSettings),
        "chunker": _dataclass_keys(ChunkerSettings),
        "retrieval_llm": "__retrieval_llm__",
        # #175: the card-drafting LLM follows the same usage-entry shape.
        "card_drafter": "__retrieval_llm__",
        # Issue #39: same usage-entry shape as retrieval_llm (preset
        # ref + optional model/llm override deltas).
        "vlm_llm": "__retrieval_llm__",
        # Issue #115: text LLM that reformats VLM output to clean Markdown.
        "vlm_format_llm": "__retrieval_llm__",
        # Issue #284: the multimodal model driving the make_deck build loop.
        "deck_vlm": "__retrieval_llm__",
        # Issue #105: the doc-quality judge follows the same usage-entry shape.
        "quality_judge": "__retrieval_llm__",
        # Issue #56: wiki LLM follows the same preset-reference pattern
        # (`llm` is a usage-entry ref); the step budgets are scalar
        # leaves alongside it.
        "wiki": {
            "llm": "__retrieval_llm__",
            "maintainer_max_turns": set(),
            "reader_max_turns": set(),
        },
        "retrieval": {
            "enhancements": {
                "expand": _dataclass_keys(EnhancementInt),
                "hyde": _dataclass_keys(EnhancementInt),
                "rerank": _dataclass_keys(EnhancementBool),
            },
            # #105: scalar leaves (float, and int|null) — the shape walk skips
            # their non-dict values, like `max_searches_per_turn` below.
            "quality_weight": set(),
            "quality_floor": set(),
        },
        # Issue #195: scalar leaf (int or null). The shape walk skips its
        # non-dict value; the value range is checked by `_check_max_searches`.
        "max_searches_per_turn": set(),
        # Issue #334: scalar leaf (positive int). Range checked by `_check_max_searches`.
        "max_searches_ceiling": set(),
        "code_embedder": _dataclass_keys(CodeEmbedderSettings),
        "git": _dataclass_keys(GitSettings),
        # Issue #39: `kb.parsers` / `kb.parsers_disabled` are
        # list-of-strings leaves. The shape check below skips non-dict
        # values, so an empty set is enough to whitelist the key
        # without trying to recurse into the list.
        "parsers": set(),
        "parsers_disabled": set(),
    },
    # Issue #51: list-of-strings leaves (same shape rationale as
    # kb.parsers above).
    "health": {
        "checks": set(),
        "checks_disabled": set(),
        # #231: the Diagnostics AI judge follows the same usage-entry shape as
        # kb.retrieval_llm / kb.card_drafter (preset ref + optional overrides).
        "judge_llm": "__retrieval_llm__",
    },
    "agents": "__agents__",  # sentinel — handled by _check_agents_keys
    # Issue #58/#59: durable wiki-maintenance queue backend selection.
    "message_queue": {
        "kind": set(),
        "rabbitmq": _dataclass_keys(RabbitmqSettings),
    },
    # Observability feature B: the faithful LLM call log.
    "observability": {
        "llm_log": _dataclass_keys(LlmLogSettings),
    },
    # #196 busy-aware failover global defaults.
    "failover": _dataclass_keys(FailoverSettings),
}


def _check_unknown_keys(node: dict[str, Any], schema: Any, *, prefix: str, source: str) -> None:
    """Walk the merged dict; at each level the schema tells us what
    keys are allowed. Unknown key → raise with full dotted path."""
    if schema == "__agents__":
        _check_agents_keys(node, prefix=prefix, source=source)
        return
    if schema == "__retrieval_llm__":
        _check_retrieval_llm_dict(node, prefix=prefix, source=source)
        return
    if isinstance(schema, set):
        # Leaf section: every key must be in the allowed set.
        for key in node:
            if key not in schema:
                _raise_unknown(prefix, key, sorted(schema), source)
        return
    # schema is a dict — sub-sections.
    assert isinstance(schema, dict)
    for key, value in node.items():
        if key not in schema:
            _raise_unknown(prefix, key, sorted(schema), source)
        if not isinstance(value, dict):
            # Type mismatch — leaf where dict was expected, the dataclass
            # constructor will fail. Skip here; deeper-key check N/A.
            continue
        sub = schema[key]
        _check_unknown_keys(value, sub, prefix=_join(prefix, key), source=source)


def _check_agents_keys(node: dict[str, Any], *, prefix: str, source: str) -> None:
    """`agents:` shape (B-flat): `presets` is reserved (the recipes
    dict); every other key is a SUB-AGENT PURPOSE — a usage list. The
    validator doesn't know the legal purpose names ahead of time;
    operators add new sub-agents by writing `agents.<new_purpose>: [...]`
    and the catalog wires them by name."""
    for preset_name, preset_dict in node.get("presets", {}).items():
        if not isinstance(preset_dict, dict):
            continue
        _check_preset_dict(
            preset_dict, prefix=_join(prefix, f"presets.{preset_name}"), source=source
        )
    # Every non-`presets` key is treated as a usage list (purpose). Both
    # legacy single-dict and list-of-dicts shapes are accepted (the merge
    # step normalises the single-dict form when it lands).
    for purpose, entries_node in node.items():
        if purpose == "presets":
            continue
        if isinstance(entries_node, dict):
            _check_usage_dict(entries_node, prefix=_join(prefix, purpose), source=source)
        elif isinstance(entries_node, list):
            entries: list[Any] = entries_node
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                _check_usage_dict(entry, prefix=_join(prefix, f"{purpose}[{i}]"), source=source)


def _check_preset_dict(d: dict[str, Any], *, prefix: str, source: str) -> None:
    for key in d:
        if key not in _PRESET_FIELDS:
            _raise_unknown(prefix, key, sorted(_PRESET_FIELDS), source)
    llm = d.get("llm")
    if isinstance(llm, dict):
        for key in llm:
            if key not in _PRESET_LLM_FIELDS:
                _raise_unknown(_join(prefix, "llm"), key, sorted(_PRESET_LLM_FIELDS), source)


def _check_usage_dict(d: dict[str, Any], *, prefix: str, source: str) -> None:
    """Usage entries (workspace_chat[], kb_chat): `preset` + `name`
    plus any subset of Preset fields (overrides)."""
    for key in d:
        if key not in _USAGE_FIELDS:
            _raise_unknown(prefix, key, sorted(_USAGE_FIELDS), source)
    llm = d.get("llm")
    if isinstance(llm, dict):
        for key in llm:
            if key not in _PRESET_LLM_FIELDS:
                _raise_unknown(_join(prefix, "llm"), key, sorted(_PRESET_LLM_FIELDS), source)
    enh = d.get("enhancements")
    if isinstance(enh, dict):
        for key in enh:
            if key not in _ENHANCEMENT_KEYS:
                _raise_unknown(
                    _join(prefix, "enhancements"), key, sorted(_ENHANCEMENT_KEYS), source
                )


def _check_preset_references(merged: dict[str, Any], *, source: str) -> None:
    """Every usage entry's `preset` (across all purposes) must resolve
    to a known preset name."""
    agents = merged.get("agents", {})
    presets = agents.get("presets", {})
    known = set(presets)
    for purpose, entries_node in agents.items():
        if purpose == "presets":
            continue
        # Single-dict legacy form is accepted by the merge step; defensively
        # wrap it here so the loop is uniform.
        entries = (
            [entries_node]
            if isinstance(entries_node, dict)
            else (entries_node if isinstance(entries_node, list) else [])
        )
        for i, entry in enumerate(entries):
            ref = entry.get("preset") if isinstance(entry, dict) else None
            if ref is not None and ref not in known:
                raise ValueError(
                    f"config {source}: agents.{purpose}[{i}].preset "
                    f"references unknown preset {ref!r}; "
                    f"known presets: {sorted(known)}"
                )


def _check_preset_fallbacks(merged: dict[str, Any], *, source: str) -> None:
    """Every name in a preset's `fallbacks` chain (#196) must resolve to another
    known preset, and a preset must not list itself (a degenerate cycle that
    would retry the same busy model). No deeper recursion is checked because the
    chain is not expanded — a fallback's own `fallbacks` are ignored."""
    presets = merged.get("agents", {}).get("presets", {})
    known = set(presets)
    for name, d in presets.items():
        if not isinstance(d, dict):
            continue
        for fb in d.get("fallbacks", []) or []:
            if fb == name:
                raise ValueError(f"config {source}: agents.presets.{name}.fallbacks lists itself")
            if fb not in known:
                raise ValueError(
                    f"config {source}: agents.presets.{name}.fallbacks references "
                    f"unknown preset {fb!r}; known presets: {sorted(known)}"
                )


def _check_preset_required_fields(merged: dict[str, Any], *, source: str) -> None:
    """Every preset must have `model`. `prompt_file` is optional —
    LLM-only presets (e.g. `kb-retrieval`) legitimately omit it; agent
    callers that need a prompt enforce it themselves at catalog build."""
    for name, d in merged.get("agents", {}).get("presets", {}).items():
        if not isinstance(d, dict):
            continue
        if "model" not in d:
            raise ValueError(
                f"config {source}: agents.presets.{name} missing required field 'model'"
            )


_RETRIEVAL_LLM_FIELDS = {"preset", "model", "llm", "reasoning_effort"}
_REASONING_EFFORTS = {"none", "low", "medium", "high"}


def _check_retrieval_llm_dict(node: dict[str, Any], *, prefix: str, source: str) -> None:
    """`kb.retrieval_llm` accepts the usage-entry subset relevant to a
    pure LLM endpoint: `preset` (required), `model` and `llm` (override
    deltas), and `reasoning_effort` (none|low|medium|high — none disables
    the model's thinking for kb_search's expansions). Prompt / tools /
    suggestions are NOT exposed here — the retriever doesn't read them, and
    surfacing them would imply they take effect."""
    for key in node:
        if key not in _RETRIEVAL_LLM_FIELDS:
            _raise_unknown(prefix, key, sorted(_RETRIEVAL_LLM_FIELDS), source)
    effort = node.get("reasoning_effort")
    if effort and effort not in _REASONING_EFFORTS:
        raise ValueError(
            f"config {source}: {_join(prefix, 'reasoning_effort')}={effort!r} invalid; "
            f"use one of {sorted(_REASONING_EFFORTS)} (none disables the model's thinking)"
        )
    llm = node.get("llm")
    if isinstance(llm, dict):
        for key in llm:
            if key not in _PRESET_LLM_FIELDS:
                _raise_unknown(_join(prefix, "llm"), key, sorted(_PRESET_LLM_FIELDS), source)


def _check_retrieval_llm_reference(merged: dict[str, Any], *, source: str) -> None:
    """`kb.retrieval_llm.preset` / `kb.vlm_llm.preset` / `kb.wiki.llm.preset`
    (when set) must resolve to a known preset name — mirrors
    `_check_preset_references` for the other usage-entry sites. Each pair
    is (dotted-path, the ref dict)."""
    known = set(merged.get("agents", {}).get("presets", {}))
    kb = merged.get("kb", {})
    wiki = kb.get("wiki")
    refs = [
        ("kb.retrieval_llm", kb.get("retrieval_llm")),
        ("kb.card_drafter", kb.get("card_drafter")),
        ("kb.vlm_llm", kb.get("vlm_llm")),
        ("kb.vlm_format_llm", kb.get("vlm_format_llm")),
        ("kb.deck_vlm", kb.get("deck_vlm")),
        ("kb.quality_judge", kb.get("quality_judge")),
        ("kb.wiki.llm", wiki.get("llm") if isinstance(wiki, dict) else None),
    ]
    for path, ref in refs:
        if not isinstance(ref, dict):
            continue
        preset_name = ref.get("preset")
        if preset_name is None:
            raise ValueError(
                f"config {source}: {path}.preset is required (set {path}: null to disable)"
            )
        if preset_name not in known:
            raise ValueError(
                f"config {source}: {path}.preset references "
                f"unknown preset {preset_name!r}; known presets: {sorted(known)}"
            )


def _raise_unknown(prefix: str, key: str, valid: list[str], source: str) -> None:
    path = _join(prefix, key)
    raise ValueError(
        f"config {source}: unknown key {path!r}; valid keys at {prefix or '<root>'}: {valid}"
    )


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


# ─── construction ───────────────────────────────────────────────────────


def _settings_from_dict(d: dict[str, Any]) -> Settings:
    """Build a typed `Settings` tree from the merged dict. Each
    sub-section is built by `_build(SectionCls, sub_dict)`."""
    return Settings(
        server=_build(ServerSettings, d["server"]),
        sandbox=_build_sandbox(d["sandbox"]),
        sandbox_host=_build(SandboxHostSettings, d["sandbox_host"]),
        tools=_build(ToolsSettings, d["tools"]),
        filestore=_build(FilestoreSettings, d["filestore"]),
        runner=_build(RunnerSettings, d["runner"]),
        llm=_build(LlmSettings, d["llm"]),
        read_file=_build(ReadFileSettings, d["read_file"]),
        exec=_build(ExecSettings, d["exec"]),
        history=_build(HistorySettings, d["history"]),
        kb=KbSettings(
            embedder=_build(EmbedderSettings, d["kb"]["embedder"]),
            chunker=_build(ChunkerSettings, d["kb"]["chunker"]),
            retrieval_llm=_build_retrieval_llm(d["kb"]["retrieval_llm"]),
            card_drafter=_build_retrieval_llm(d["kb"].get("card_drafter")),
            retrieval=_build_retrieval(d["kb"]["retrieval"]),
            max_searches_per_turn=d["kb"]["max_searches_per_turn"],
            max_searches_ceiling=d["kb"]["max_searches_ceiling"],
            code_embedder=_build(CodeEmbedderSettings, d["kb"]["code_embedder"]),
            git=_build(GitSettings, d["kb"]["git"]),
            vlm_llm=_build_retrieval_llm(d["kb"]["vlm_llm"]),
            vlm_format_llm=_build_retrieval_llm(d["kb"].get("vlm_format_llm")),
            deck_vlm=_build_retrieval_llm(d["kb"].get("deck_vlm")),
            wiki=_build_wiki(d["kb"]["wiki"]),
            parsers=list(d["kb"].get("parsers", [])),
            parsers_disabled=list(d["kb"].get("parsers_disabled", [])),
        ),
        agents=AgentsSettings(
            presets={name: _build_preset(p) for name, p in d["agents"]["presets"].items()},
            sub_agents={
                purpose: _normalize_usage_list(entries)
                for purpose, entries in d["agents"]["sub_agents"].items()
            },
        ),
        health=HealthSettings(
            checks=list(d.get("health", {}).get("checks", [])),
            checks_disabled=list(d.get("health", {}).get("checks_disabled", [])),
            judge_llm=_build_retrieval_llm(d.get("health", {}).get("judge_llm")),
        ),
        message_queue=_build_message_queue(d["message_queue"]),
        observability=ObservabilitySettings(
            llm_log=_build(LlmLogSettings, d["observability"]["llm_log"]),
        ),
        failover=_build(FailoverSettings, d["failover"]),
    )


def _build(cls, sub: dict[str, Any]):
    """Construct a leaf dataclass — sub_dict's keys must all be field
    names (validated upstream). Extra fields → schema validator caught;
    missing fields → dataclass default applies."""
    return cls(**sub)


def _build_sandbox(d: dict[str, Any]) -> SandboxSettings:
    """`sandbox` has two nested dataclasses (`http`, #345 `isolation`), so it
    can't use the flat `_build`. `http` is None unless `kind: http` declares the
    client block; `isolation` always builds (its own defaults when the operator
    omits the block)."""
    http = d.get("http")
    iso = d.get("isolation")
    flat = {k: v for k, v in d.items() if k not in ("http", "isolation")}
    return SandboxSettings(
        **flat,
        http=HttpSandboxSettings(**http) if http is not None else None,
        isolation=SandboxIsolationSettings(**iso)
        if iso is not None
        else SandboxIsolationSettings(),
    )


def _normalize_usage_list(raw: Any) -> list[dict[str, Any]]:
    """Sub-agent usage lists are LIST of usage entries; the legacy
    single-dict shape (issue #32 pre-list) is also accepted and
    wrapped into a single-entry list — so an operator who only wants
    one entry can keep writing it as one dict."""
    if isinstance(raw, dict):
        return [dict(raw)]
    if isinstance(raw, list):
        return [dict(e) for e in raw if isinstance(e, dict)]
    return []


def _build_retrieval(d: dict[str, Any]) -> RetrievalSettings:
    e = d["enhancements"]
    return RetrievalSettings(
        enhancements=EnhancementSettings(
            expand=_build(EnhancementInt, e["expand"]),
            hyde=_build(EnhancementInt, e["hyde"]),
            rerank=_build(EnhancementBool, e["rerank"]),
        ),
    )


def _build_retrieval_llm(d: Any) -> RetrievalLlmRef | None:
    """`kb.retrieval_llm: null` → `None`; otherwise build the typed
    `RetrievalLlmRef`. Validator upstream guarantees `preset` is present
    when `d` is a dict, so KeyError here would be a loader bug. An
    explicit `llm: null` inside the ref is treated as "no per-ref
    creds" — same as omitting the `llm` key."""
    if d is None:
        return None
    assert isinstance(d, dict), f"retrieval_llm must be null or dict, got {type(d).__name__}"
    llm_dict = d.get("llm") or {}
    return RetrievalLlmRef(
        preset=d["preset"],
        model=d.get("model", ""),
        llm=PresetLlmSettings(
            base_url=llm_dict.get("base_url", ""),
            api_key=llm_dict.get("api_key", ""),
        ),
        # "" = unset (explicit YAML null also → unset); "none"/low/medium/high
        # control kb_search's retrieval-LLM thinking.
        reasoning_effort=d.get("reasoning_effort") or "",
    )


def _build_wiki(d: dict[str, Any]) -> WikiSettings:
    """Build `kb.wiki` — `llm` reuses the retrieval-llm ref builder
    (so `null` disables the wiki); the budgets are plain ints."""
    return WikiSettings(
        llm=_build_retrieval_llm(d["llm"]),
        maintainer_max_turns=d["maintainer_max_turns"],
        reader_max_turns=d["reader_max_turns"],
    )


def _build_message_queue(d: dict[str, Any]) -> MessageQueueSettings:
    return MessageQueueSettings(
        kind=d["kind"],
        rabbitmq=_build(RabbitmqSettings, d["rabbitmq"]),
    )


def _build_preset(d: dict[str, Any]) -> Preset:
    raw_at = d.get("allowed_tools")
    return Preset(
        model=d["model"],
        prompt_file=d.get("prompt_file", ""),
        description=d.get("description", ""),
        suggestions=list(d.get("suggestions", [])),
        # Tri-state preservation: None survives so the catalog resolver
        # can hand the runner the same "haven't specified" signal the
        # operator wrote (or didn't write) in their YAML.
        allowed_tools=list(raw_at) if raw_at is not None else None,
        env=dict(d.get("env", {})),
        sandbox_image=d.get("sandbox_image", "workspace-app/sandbox:py312-ds"),
        idle_timeout_seconds=d.get("idle_timeout_seconds", 28800),
        llm=PresetLlmSettings(
            base_url=d.get("llm", {}).get("base_url", ""),
            api_key=d.get("llm", {}).get("api_key", ""),
            frequency_penalty=d.get("llm", {}).get("frequency_penalty"),
            presence_penalty=d.get("llm", {}).get("presence_penalty"),
            repetition_penalty=d.get("llm", {}).get("repetition_penalty"),
        ),
        fallbacks=list(d.get("fallbacks", [])),
        ttft_timeout_s=d.get("ttft_timeout_s"),
        cooldown_s=d.get("cooldown_s"),
        idle_timeout_s=d.get("idle_timeout_s"),
    )

"""App manifest — the typed shape of an App's ``app.json`` (#89).

Carries everything the platform needs to present + drive an App that the
hand-written ``model.py`` (the WorkItem Struct) does NOT: identity (for the
launcher + theming), function toggles, the agent ceiling (picker / tools / base
prompt), item display nouns, and the per-surface field ``layout`` + ``labels``
display overlay. Field *types/options* are read from the model's OpenAPI schema,
NOT restated here (decision 19).
"""

from __future__ import annotations

from importlib import resources

import msgspec
from msgspec import UNSET, Struct, UnsetType, field

from ..resources.agent_config import Suggestion

_APPS_PKG = "workspace_app.apps"
_MANIFEST_FILE = "app.json"


class FunctionToggles(Struct):
    """Capability/UI gates. ``terminal`` requires ``sandbox`` (validated at
    catalog build, not here)."""

    workspace: bool = True  # file IDE + file tools + profile file-seeding
    sandbox: bool = True  # agent `exec` + package tools
    terminal: bool = True  # human shell pane (needs sandbox)


class PickerEntry(Struct):
    """One entry in the App's model picker — references a config.yaml preset by
    name (model + creds) and gives it a display ``name``."""

    preset: str
    name: str


class AgentManifest(Struct):
    """The App's agent *ceiling*. A profile narrows ``tools`` / ``picker`` to a
    subset; the preset supplies model + creds (3-layer resolve, decision 25)."""

    prompt_file: str  # base system prompt, relative to the app dir
    tools: list[str] = field(default_factory=list)
    picker: list[PickerEntry] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    """App-level quick-prompt fallback — used when the chosen profile declares no
    suggestions of its own (decision 5b: suggestions are profile-level, App gives
    a fallback)."""
    context_files: list[str] = field(default_factory=list)
    """Topic Hub §6 — workspace files (e.g. ``MEMORY.md``, ``collections.json``)
    whose live content is prepended to the agent's content each turn (deterministic
    injection; never persisted). Empty ⇒ no injection (the default for most Apps)."""


class Layout(Struct):
    """Which fields render on each surface (ordered). Omitted ``form`` → all."""

    breadcrumb: list[str] = field(default_factory=list)
    statusbar: list[str] = field(default_factory=list)
    # JSON key is "list"; the Python attr is `list_` to avoid shadowing the
    # builtin `list` (which the sibling annotations resolve against).
    list_: list[str] = field(default_factory=list, name="list")
    form: list[str] | UnsetType = UNSET
    # Files the workspace opens on entry (filtered to those that exist). Replaces
    # the shell's old hardcoded `designViews`.
    default_tabs: list[str] = field(default_factory=list)


class Lifecycle(Struct):
    """An App's close/resolve workflow (#89). The shell shows a Close affordance
    only when this is present; ``closing_states`` (a subset of the status field's
    enum) are the states Close transitions to. Absent → no Close."""

    status_field: str
    closing_states: list[str] = field(default_factory=list)


class ItemNouns(Struct):
    """Human-readable item names that drive the FE's strings (decision 24)."""

    noun: str
    noun_plural: str
    create_label: str | UnsetType = UNSET  # omitted → "New {noun}"


class OnboardingPoint(Struct):
    """One read-only step/highlight in an App's welcome teaching (#161)."""

    title: str
    body: str


class Onboarding(Struct):
    """Versioned, read-only welcome teaching shown when entering the App (#161).

    The FE pops it until the user permanently dismisses *this* ``version``; bumping
    ``version`` re-shows it for everyone. Content is per-App (this block); the
    platform-level welcome lives as a FE constant, not here."""

    version: str  # hand-bumped when the teaching changes (NOT a release version)
    title: str
    intro: str = ""
    points: list[OnboardingPoint] = field(default_factory=list)


class AppManifest(Struct):
    slug: str
    title: str
    agent: AgentManifest
    item: ItemNouns
    onboarding: Onboarding | None = None
    description: str = ""
    icon: str = ""  # "icon.svg" (file) | emoji | named-icon key
    color: str = ""  # hex → --accent trio (full re-theme inside the App)
    function: FunctionToggles = field(default_factory=FunctionToggles)
    layout: Layout = field(default_factory=Layout)
    labels: dict[str, str] = field(default_factory=dict)
    # Display overlay: enum field -> {option -> tone token} (err/warn/ok/info/
    # muted), so an App's chip palette (e.g. RCA severity P0=err) is DATA, not
    # shell code. The FE styles a `select` field's chip from this; absent →
    # neutral.
    field_styles: dict[str, dict[str, str]] = field(default_factory=dict)
    lifecycle: Lifecycle | None = None
    default_profile: str = "default"


def load_app_manifest(slug: str) -> AppManifest:
    """Decode ``apps/<slug>/app.json`` into a typed ``AppManifest``."""
    raw = (resources.files(_APPS_PKG) / slug / _MANIFEST_FILE).read_bytes()
    return msgspec.json.decode(raw, type=AppManifest)

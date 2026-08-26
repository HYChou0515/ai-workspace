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
from importlib.resources.abc import Traversable
from typing import Literal

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


class AppResources(Struct):
    """What ONE item of this App is allowed to consume — the App's own statement
    of its appetite, the way a k8s Pod states ``requests``/``limits``.

    Named ``resources`` rather than ``sandbox`` in app.json for two reasons: the
    manifest already has a ``function.sandbox`` boolean (a capability gate — a
    different thing entirely), and ``disk`` bounds the item's *workspace*, which
    outlives every sandbox it is ever mirrored into.

    A data-analysis App genuinely needs more memory than a chat App, and that
    fact belongs with the App, not in a deploy-side table keyed by slug (which
    a new App would have to remember to edit). The deploy still has the last
    word: ``resources.per_app.max`` in config.yaml is a ceiling, and a value
    above it fails at BOOT rather than being trimmed behind the operator's back.

    Every field is optional and falls through on its own — an App that only
    cares about memory does not restate cpu/disk. Absent ⇒ the deploy's
    ``resources.per_app.default``, then today's knobs
    (``sandbox.isolation.*`` / ``filestore.workspace_quota``), so an App that
    declares nothing runs exactly as it does today.
    """

    cpu: float = 0.0  # cores; 0 ⇒ not declared
    memory: str = ""  # "512M" / "2G"; "" ⇒ not declared
    disk: str = ""  # workspace quota for ONE item; "" ⇒ not declared


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
    external_tools: dict[str, str] = field(default_factory=dict)
    """#674: third-party tools, as `{local name: artifact manifest url}`.

    The KEY is this deployment's name for the tool and belongs in `tools`
    like any other; the value only says where its bytes come from. Naming it
    here rather than trusting the artifact's own name is what lets two
    authors both ship a `data-fetch` without one shadowing the other.

    Changing a tool's VERSION needs no edit here: the url points at the
    latest artifact, and the next sandbox picks it up. Only adding or
    removing a tool is a change to this file."""
    picker: list[PickerEntry] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    """App-level quick-prompt fallback — used when the chosen profile declares no
    suggestions of its own (decision 5b: suggestions are profile-level, App gives
    a fallback)."""
    context_files: list[str] = field(default_factory=list)
    """Topic Hub §6 — workspace files (e.g. ``MEMORY.md``, ``collections.json``)
    whose live content is prepended to the agent's content each turn (deterministic
    injection; never persisted). Empty ⇒ no injection (the default for most Apps)."""
    skills: list[str] = field(default_factory=list)
    """#298 Q7 — built-in (shared) skills this App opts into, by name, from the
    ``workspace_app.apps.shared_skills.SHARED_SKILLS`` registry (introduced like a
    tool-package: list the name to include it). They're advertised in the skill
    index alongside the profile's own ``.skill/`` skills + the user's workspace
    skills. ``author-skill`` (the co-authoring meta-skill) is the one v1 ships."""


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
    # #419 §B5: the App's declarative views (`views/*.ai.yaml`) that are its main
    # screen, ordered — so they aren't buried as files the user must hunt for.
    # When `primary_surface` is "views" the shell opens these up front as the
    # main-stage tabs instead of `default_tabs`. Empty for apps with no views.
    views: list[str] = field(default_factory=list)
    # #159 / #419: which surface leads when an item opens. "chat" (default) makes
    # the agent chat the main stage and tucks the file IDE behind a `Workspace`
    # toggle; "ide" opens the VS Code workspace up front (RCA's evidence/brief/
    # notebook flow); "views" (#419 §B5) opens the App's `layout.views` as the
    # main stage (PM's board / gantt / roadmap). Ignored when
    # `function.workspace` is false (no files to show); "ide"/"views" require
    # workspace=true, and "views" requires a non-empty `views` (validated at
    # catalog build).
    primary_surface: Literal["chat", "ide", "views"] = "chat"
    # #200: how prominent the per-item multi-chat switcher is. "auto" (default)
    # keeps the switcher hidden until a second chat exists, so a normal App feels
    # single-chat while staying multichat-capable — a wedged chat is never a dead
    # end (the "+ New chat" escape lives in the chat header). "always" surfaces the
    # switcher up front for multi-chat-first Apps (Topic Hub). Drives FE chrome
    # only; every App is multichat-capable regardless of this value.
    chat_switcher: Literal["auto", "always"] = "auto"


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
    # A file the App ships beside app.json ("icon.png", "icon.svg", … — served
    # from GET /apps/{slug}/icon), an emoji, or a named-icon key.
    icon: str = ""
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
    # What one item of this App may consume. Absent ⇒ the deploy's defaults;
    # see `AppResources` and `quota.limits.resolve_app_limits`.
    resources: AppResources | None = None


def apps_root() -> Traversable:
    """Where the bundled Apps live. A function, not a constant, so a test can
    point the loaders at a temp tree without touching the installed package."""
    return resources.files(_APPS_PKG)


def load_app_manifest(slug: str) -> AppManifest:
    """Decode ``apps/<slug>/app.json`` into a typed ``AppManifest``."""
    raw = (apps_root() / slug / _MANIFEST_FILE).read_bytes()
    return msgspec.json.decode(raw, type=AppManifest)


# What an App may ship its `icon` as. The extension decides the media type, so
# the browser gets a real `Content-Type` — SVG is served as a file like any
# other image rather than inlined into the manifest JSON.
ICON_MEDIA_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def load_app_icon(slug: str, icon: str) -> tuple[bytes, str] | None:
    """The bytes + media type of an App's file-based ``icon``, or ``None`` when
    there is no file to serve.

    ``None`` covers every way an icon is not a shipped image — a named-icon key
    or an emoji (the other two manifest forms), an extension we don't serve, and
    a manifest naming a file that isn't there. The caller turns all of them into
    one 404, so a mis-typed filename degrades to the FE's fallback glyph instead
    of a 500.

    An icon is a plain filename BESIDE ``app.json``: anything with a separator
    is refused outright rather than resolved, so a manifest can never reach out
    of its own App directory.
    """
    suffix = icon[icon.rfind(".") :].lower() if "." in icon else ""
    media_type = ICON_MEDIA_TYPES.get(suffix)
    if media_type is None or "/" in icon or "\\" in icon:
        return None
    path = apps_root() / slug / icon
    if not path.is_file():
        return None
    return path.read_bytes(), media_type

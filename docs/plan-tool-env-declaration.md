# Plan — what a tool needs from the environment, and a way to fill it (#750)

## Problem

A tool needs environment variables to work, and **nothing declares which**.
`CommandInfo` is `{name, description, params_json_schema}`; `PackageInfo` is
`{name, commands, install_dir}`. Every field describes what the *LLM* passes in.
Not one describes what the *environment* must already hold.

So `EnvVarsModal` is a free-text box with no reference table — deliberately one
box of `.env` text, because what people actually do is paste a block in from
somewhere else. That is a good editor and a bad discovery mechanism. With a
handful of tools installed, the only way to learn a variable's name is to run
the tool and read the failure.

And some values a user cannot type at all: they know their own account and
password; the tool wants the token that a login exchanges them for.

## Position — this is a convenience, not a gate

Stated first because it rules out half the design space. The declaration
**never filters, never blocks, never gates**. It does not have to be correct; it
has to be useful.

The principle through every decision: **make it better for the diligent author
without making it worse for the lazy one.** Declare nothing and you get today's
behaviour. Declare names and the panel grows fields. Add a sentence and the
fields explain themselves. Add `required` and the panel can say "everything
needed is set".

## Goal

1. The env panel shows, per tool that declared, which variables it wants — as a
   form, so filling them does not require knowing the names by heart.
2. A tool's variables can be obtained by logging in: a button, a dialog, and the
   resulting values land in the panel.
3. Credentials are never stored.
4. Storage is untouched: still `WorkItemBase.env_vars`, still a flat
   `dict[str, str]`.

## Locked design (from /grill-me, 2026-09-01 — full record in #750)

### The declaration lives in the tool package, hand-written

Only the author knows which variables their code reads, and since #674 the
author is a third party shipping an artifact. A declaration held anywhere else
would have to be maintained by someone without that knowledge — which is the
situation this issue exists to end, just moved to a different person.

`env.json` beside the package source, copied verbatim into the bundle by
prebuild, read by `discover_packages` onto `PackageInfo`:

```json
[
  { "name": "SAP_HOST",
    "description": "SAP server address, e.g. sap.corp.example.com",
    "required": true },
  { "name": "HTTP_PROXY",
    "description": "only if your subnet needs a proxy to reach SAP",
    "required": false }
]
```

`name` is the only required key. **Copied, not derived**: deriving it from the
code (the way `params_json_schema` is derived from pydantic) would make the
declaration impossible to desynchronise — and that is the property of a
contract, not of a hint. Rejected deliberately; see #750.

### Absent is not empty

A package with no `env.json` declares **nothing known**, which is NOT the same
as **needs nothing**. Three states, and the UI must never collapse the third
into the second:

| state | UI |
|---|---|
| declared, needs these | fields, with descriptions |
| declared, needs nothing | nothing for this tool |
| **no declaration** | named as "did not say", never as "needs nothing" |

### `required` informs; it never blocks

Marked-required-and-empty renders as *not yet filled*, never as an error. Save
stays enabled, the tool still dispatches, the field gets no red border. A red
border is itself a gate: the button works, but the screen says it should not be
pressed, so nobody presses it.

Its only job is to stop the panel crying wolf. A tool with 2 required and 5
optional variables would otherwise report "5 missing" forever, and a panel that
always complains is a panel nobody reads.

### The panel is a second view over the same storage

Same modal, same `env_vars`, same `.env` parsing, **no schema change**. The
free-text box stays; the form grows above it.

Tabs are a **filter**, not separate forms — someone who just enabled one tool
wants to fill that tool's variables without scrolling past everything else. The
state behind a field is keyed by **variable name**, so a variable two tools share
is one value: editing it under either tab edits the same thing, and both tabs
label it with who else uses it. Without that label, clearing a variable under
tool A silently breaks tool B.

Which tools appear follows the item's current toolset (the tri-state picker).

### Credentials: second party implements, third party never names it

**password → second-party impl → environment variable → third-party tool.**

A deploy registers implementations through a dotted-path list (the existing
`kb.parsers` / `health.checks` / `server.request_env` convention). A list, not a
single entry: one deploy can have an SAP login, an AD login, and an API-key
exchange.

```python
class IEnvProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def label(self) -> str: ...
    """Shown on the button, e.g. "SAP production login"."""

    @property
    @abc.abstractmethod
    def produces(self) -> frozenset[str]: ...
    """The variable NAMES this can fill. The only join with a tool."""

    @property
    @abc.abstractmethod
    def inputs(self) -> tuple[InputField, ...]: ...
    """What to ask the user for — the provider owns its own form."""

    @abc.abstractmethod
    async def resolve(self, values: dict[str, str]) -> dict[str, str]: ...
    """Exchange the inputs for variables. Values are never persisted."""
```

**A tool never names a provider.** It declares variable names; a provider
declares the names it produces; the panel joins on the name. A tool that wrote
`"filled_by": "sap-login"` would be writing into an identifier namespace shared
with a second party it has never met — and the landing site of a collision is a
login dialog. Worse, it would let a third party decide which credential our
interface asks the user for, in a sentence carrying our credibility. The full
argument is in #750.

The cost is that the *name* becomes the contract, so two unrelated systems both
calling their variable `API_TOKEN` will match wrongly. That collision already
exists — `env_vars` is a flat dict, one value per name, and today those two
systems silently overwrite each other with no indication at all. Name-matching
only makes it visible: **two providers producing one name draw two buttons and
guess nothing**, because picking wrong means typing a production password into a
test system.

`resolve`'s whole return is written, unfiltered. Filtering to declared names
would drop exactly what an incomplete declaration most needs to keep.

### Filled, not saved

The dialog's result populates the form; the user still presses Save. The modal
is already a draft surface (edit, reconsider, close without effect), and Import
already merges into the box rather than persisting. An action that wrote through
would put two different save semantics in one dialog with nothing on screen
distinguishing them.

## Trust boundary — and the hole that is already there

The tool never sees the credential. It **does** see the credential's product:
`tooling.registry._tool_env` hands the entire `user_env` to every dispatched
tool, with no per-tool scoping.

That is pre-existing and this feature does not widen it, but it is why **no
"which tools may trigger which providers" allowlist is included**: that lock
holds nothing. Blocking tool X's button does not stop tool X reading `AD_TOKEN`
once a legitimate tool got the user to fill it. The only effective form is
per-tool scoping of `user_env`, which decision "absent is not empty" forecloses
— every existing tool declares nothing and would receive nothing.

## Out of scope

- `IRequestEnv` (#714) — that is "someone else decides who you are"; this is
  "you know what you need to type". Confirmed not applicable.
- Narrowing "every variable to every tool". It is the right thing to narrow, and
  it is a different issue.
- Masking `env_vars`. Plaintext to any `read_meta` holder is a decision recorded
  in #673.

## Edge cases

| case | behaviour |
|---|---|
| package has no `env.json` | tool listed as "did not declare", not as "needs nothing" |
| `env.json` is malformed | prebuild fails loudly, naming the package (same posture as a half-built bundle in `discover_packages`) |
| two tools want one variable | one field, labelled with both; one value |
| a tool declares a name no provider produces | field only, no button — typing it always works |
| a tool declares a name whose provider this deploy lacks | same: field only. A tool from elsewhere stays usable |
| two providers produce one name | both buttons, each with its own label. Never auto-pick |
| `resolve` raises | the dialog reports it; nothing is filled; the panel is untouched. **Not a 502/503/504** — the FE's `GATEWAY_CUT` reads those as a cut connection and waits forever (#714's trap) |
| `resolve` returns a name nobody declared | written anyway |
| user disables a tool | its tab goes; its exclusive variables leave the form; shared ones stay, still labelled |

## Definition of Done

- [ ] A sample tool ships an `env.json`; its variables appear in the panel with
      their descriptions, under that tool's tab.
- [ ] A tool with no declaration is visibly "did not say", not "needs nothing".
- [ ] A variable declared by two tools shows once, labelled with both, and
      editing it under either tab changes one value.
- [ ] A required-and-empty field: Save enabled, no red, tool still dispatches.
- [ ] A sample `IEnvProvider` fills its variables into the form; the panel is
      still dirty afterwards and nothing is stored until Save.
- [ ] Credentials appear in no DB row, no SSE frame, no log line (probe each,
      each probe proven by a mutant first).
- [ ] With the knob unset, OpenAPI and the panel are unchanged from master.
- [ ] **Driven in a real browser by hand**: fill a form, press the login button,
      cancel it, press it again, save, reopen. A screenshot per step.

## TDD phases (flat integer; one commit per phase)

**Phase 1** — the declaration travels. `env.json` in a sample tool source →
copied by `prebuild` into the bundle → parsed by `discover_packages` onto
`PackageInfo`. Malformed input fails the build, naming the package. No UI.

**Phase 2** — the backend answers "what does this item's toolset want". One
read-only endpoint returning declared needs for the item's enabled tools, each
with the tools that asked for it, plus the tools that declared nothing. Pure
computation over existing state.

**Phase 3** — the panel grows the form. Tabs as a filter, name-keyed state,
shared-variable labels, three states rendered distinctly, `required` informing
without blocking. Storage untouched.

**Phase 4** — the provider seam. `IEnvProvider`, `server.env_providers` as a
dotted-path list, and the endpoint that runs one. ⚠️ `__main__.py` must contain
the literal `settings.server.env_providers` — `tests/config/test_server_settings_
are_wired.py` is a source-text check and a factory that takes `Settings` reads as
"not wired" (#714 hit this).

**Phase 5** — the button. Rendered only where a provider's `produces` meets a
declared need; its dialog is built from `inputs`; the result fills the form and
leaves it unsaved.

**Phase 6** — docs. `docs/extending-the-platform.md` gets the author-facing
`env.json` reference and the deployer-facing `IEnvProvider` reference, with a
worked example that is copy-pasteable verbatim.

## Key files

| file | why |
|---|---|
| `src/workspace_app/tooling/prebuild.py` | copies `env.json` into the bundle |
| `src/workspace_app/tooling/registry.py` | `PackageInfo`, `discover_packages`; `_tool_env` is the unscoped hand-off named above |
| `src/workspace_app/tooling/catalog.py` | how tool display metadata already reaches the FE |
| `src/workspace_app/api/request_env.py` | the seam to copy — shape, config wiring, failure posture |
| `src/workspace_app/factories.py` | `_construct_dotted` |
| `src/workspace_app/config/schema.py`, `__main__.py` | the new `server.*` knob and its literal-text gate |
| `web/src/components/EnvVarsModal.tsx` | the panel; `lib/envFile.ts` is its parser |
| `sample-tools/data-fetch/` | the reference declaration lands here |

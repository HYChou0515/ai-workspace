# Plan — the `run_agent` caller picks the sub-agent's model

## Problem

A sub-agent (`.agent/<name>/AGENT.md`, invoked through the `run_agent` tool)
always runs on **the parent turn's model**: `_child_context`
(`api/subagent_run.py`) swaps only the system prompt and the tool subset on the
parent's `AgentConfig`; `SubagentDef` (`apps/subagents.py`) has no model field
at all. So delegation can narrow the *prompt* and the *tools*, but never the
*engine* — a turn on an expensive hosted model pays that model's price for a
grep-shaped sub-task, and a turn on a small local model cannot hand one hard
sub-question to a stronger engine.

## Locked decisions (from /grill-me, 2026-09-03)

| Question | Decision |
|---|---|
| Who picks, and when? | **The calling LLM, per `run_agent` call** — an optional `model` argument on the tool. Not the App author, not the end user, not the AGENT.md definition. |
| Identifier? | **Preset name** (`agents.presets` key). Never a raw model string — endpoint, creds, TTFT/idle budgets and the `fallbacks:` chain all hang off the preset; a bare string would need a second resolution path (two rules never coexist). |
| Which presets are eligible? | **A curated list, not all of `agents.presets`** — the operator's cost sovereignty over what an LLM may spend autonomously. |
| Where does the list live? | **One global list** `agents.subagent_models: [<preset>, …]`. Not per-App, not per-definition — those are second-layer restrictions to add if a need materialises. |
| Unset / empty list? | **The feature does not exist**: `run_agent`'s schema carries no `model` parameter, behaviour byte-identical to today (#537: the budget decides the tool set — an unavailable option must not appear and then refuse). |
| Bad preset name in the list? | **Fail loud at load time** (config validation), not at first use. |
| `model` argument absent at call time? | **Inherit the parent's model** — today's behaviour, unchanged. |
| Workflow agent nodes? | **Out of scope** (decided in the grill). Their caller is the workflow.json author, not an LLM at call time — a different feature. |

## Design

### The knob

```yaml
agents:
  subagent_models: [qwen3-local, claude-opus]   # preset names; order = display order
```

- Loader validates every name against `agents.presets` and refuses to start on
  an unknown one (the message names the bad entry and the known presets).
- **Beware the #748/#751 silent-drop class**: the loader's hand-written kwargs
  builders (`_build_*`) drop any field nobody adds — the resolution test must
  go through the real `yaml → load → settings` path, red first. (Bitten again
  by exactly this in #759's `rate_limit_budget_s`.)

### The tool argument

- List non-empty ⇒ `run_agent` gains optional `model: str`, **JSON-schema
  `enum` = the list**, and the tool description gains one line per preset using
  its existing `description` field (the same line the FE picker shows humans).
- Strict-schema discipline per #624/kb_search: the description states what
  happens when the argument is omitted ("the sub-agent runs on this
  conversation's model").
- Enum is provider-side validation; `run_agent_impl` still checks the value
  (small local models ignore enums) and refuses with the available names —
  same shape as its existing unknown-`agent_type` message.

### Resolution

- Chosen preset → its primary endpoint via the existing cascade
  (`resolve_llm_chain(settings, RetrievalLlmRef(preset=name))[0]` at assembly
  time — model, base_url, api_key, reasoning_effort). The resolved refs for
  the listed presets are computed **once in `create_app`/factories** and carried
  to the `run_agent` seam; no Settings access at turn time.
- `_child_context` additionally swaps those fields on the child's
  `AgentConfig`. Everything else about the child (workspace, identity,
  ceilings, empty history, forbidden tools) is untouched.
- **Failover comes for free**: `get_runner` already builds chains for *every*
  preset keyed by `(model, base_url)` (`factories.py:400-404`), so the child's
  `_agent_for` lookup finds the chosen preset's chain — including #759's 429
  hold behaviour — with zero new plumbing. A preset without `fallbacks:` takes
  the plain single-endpoint path, as everywhere else.

### Provenance

The `run_agent` tool result states the engine when it differs from the parent
(`[sub-agent ran on <preset name>]` appended, mirroring the #748 "where did
this answer come from" rule), and the existing per-turn model logging applies
to the child turn unchanged.

### Knob scope (entrypoint × honoured — per the standing rule)

| Entry point | Honours `agents.subagent_models`? |
|---|---|
| `run_agent` tool (app workspace turns) | **Yes** — this feature |
| Workflow agent nodes | No — out of scope, caller is not an LLM |
| `ask_knowledge_base` / `infer_modules` (SubagentBridge) | No — their model comes from `agents.kb_chat[]` / `agents.infer_modules[]` first entry, unchanged |
| Interactive KB chat picker | No — human-facing picker, unchanged |

`docs/configuration.md` states the Yes row *and* the No rows, so the knob can
never look dead (#759's docs note, same shape).

## Deliberately not doing

- **No AGENT.md `model:` frontmatter** — the definition author was explicitly
  not chosen as the decider; adding a second decision layer now would reopen
  the settled question.
- **No per-App or per-definition allowlist** — additive later if needed.
- **No new failover machinery** — the chosen preset's chain already exists.
- **No spend metering** — the curated list *is* the cost control; metering is a
  different feature.

## Phases (one commit each)

1. **Config**: `agents.subagent_models` — schema field, loader wiring (both
   hand-written builders), load-time validation, `docs/configuration.md`.
   Red test through the real yaml→load path first.
2. **Resolution seam**: resolve listed presets to endpoint refs at assembly;
   thread them + the chosen name through the `run_agent` seam into
   `_child_context`; child config swaps model/endpoint/creds/reasoning.
3. **Tool surface**: conditional `model` parameter (enum + per-preset
   description lines), impl-side validation message, provenance line in the
   tool result.
4. **Docs + PR**: knob-scope table, `docs/workflows-authoring.md` untouched
   (out of scope stated), PR body written last.

## Test plan (red first, targeted only)

- Loader: yaml with the list → settings carry it; unknown preset → load fails
  naming it; unset → empty (feature off).
- Schema: list non-empty ⇒ `run_agent` schema has `model` with the exact enum;
  empty ⇒ parameter absent — asserted through the real tool-build entry point,
  not a unit shortcut.
- Behaviour: absent argument ⇒ child config identical to today (byte-for-byte);
  valid argument ⇒ child runs the preset's model/endpoint (scripted-runner
  double at the model seam); invalid argument ⇒ refusal names the options.
- Failover: chosen preset with `fallbacks:` ⇒ chain lookup hits (existing
  chains map), asserted at `_agent_for` level.
- Mutation probes: drop the enum from the schema build → schema test red;
  drop the child-config swap → behaviour test red.

## Verified ground truth (file pointers)

- `apps/subagents.py` — `SubagentDef` (no model field), `SUBAGENT_FORBIDDEN_TOOLS`.
- `api/subagent_run.py:75-149` — `_child_context`: swaps prompt+tools only.
- `agent/tools.py:1156-1208` — `run_agent_impl(ctx, agent_type, prompt)`.
- `factories.py:390-417` — `get_runner` builds chains for every preset.
- `config/schema.py` `AgentsSettings` — `presets` + dynamic `sub_agents`;
  the new list is a sibling field (it is not a purpose/usage list).
- Loader hand-written builders: `config/loader.py` (`_build_*`) — the
  silent-drop trap.

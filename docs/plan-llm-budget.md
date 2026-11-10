# Plan — show a person's remaining LLM budget

> Status: grilled 2026-11-10 (D1–D9, each the user's answer or a recommendation
> they accepted). Build via `/tdd`, flat phases, one commit each.

## Problem

People spend money on the LLM with every turn, and nothing in the workspace
says how much they have left. The user's ask: an interface that **brings in**
how much budget this person still has, shown like 「剩 $11／共 $20」.

Read from `origin/master` at `14fc0ed8`:

- **The platform does not know what anyone spends.** No money or LLM-cost
  figure is recorded anywhere (`git grep -i -E "max_budget|cost_usd|response_cost"
  -- 'src/*.py'` has no match). The resource limits it does keep (`quota/`,
  `GET /me/resources`) are disk and live environments, not money. So the numbers have to come from
  outside — the deploy's LLM gateway or cost system.
- **There is a proven way to plug a deploy's own code in.** `server.request_env`
  is a dotted path to an `IRequestEnv` subclass, built once at startup by
  `factories.get_request_env` (`factories.py:1466`) → `_construct_dotted`;
  empty means the feature is absent, not degraded. Its method is
  `env_for(request, *, user_id, item_id)`, so the deploy can read the cookies
  and headers its own gateway put on the request.
- **There is a row above the item chat's composer for exactly this kind of
  glance**: `composer-status` (`AgentPanel.tsx:1097`) holds storage used
  (`UsageBar`) and how full the context is (`ContextBar`); cells with nothing
  to say render nothing, and the row's CSS draws the `·` between the ones
  present.
- **The item chat has a "the turn just ended" hook**: `useChatSession` (used
  by `useItemChat` and `useAgent`) invalidates the transport's `contextKey` /
  `todosKey` when a turn ends (`useChatSession.tsx:413-417`). The
  knowledge-base chat does NOT run on it — `useKbChat` has its own terminal
  handler (corrected in review round 1; this bullet first said both did).
- **The knowledge-base chat's composer has no such row** (`KbChatPanel.tsx`,
  `kb-composer`).

## Decisions

| # | Decision |
|---|---|
| D1 | The budget is the **LLM (AI) spending allowance**. The platform sets no limit and records no spend: both numbers come from outside. |
| D2 | A deploy plugs it in by writing a class, as with `IRequestEnv`: `server.llm_budget: "module.Class"` (an `ILlmBudget` subclass). Empty (default) ⇒ no budget anywhere — no route answer, nothing drawn. |
| D3 | The method is `budget_for(request, *, user_id) -> BudgetStatus \| None`: the person's id and the request itself, so the deploy can use the credentials its gateway attached rather than storing its own. |
| D4 | `BudgetStatus` — every field optional: `remaining`, `total`, `currency`; one plain-text `message`; one `link` (label + URL); `tone` = `info` / `warn` / `critical`. The platform formats the numbers (「剩 $11／共 $20」 — remaining FIRST, both labelled); the message is never HTML. `None` ⇒ nothing shown. A message alone (no numbers) is allowed. |
| D5 | **Shown, never enforced.** The send is not refused at zero; the gateway is what refuses. A wrong or slow answer can therefore never stop someone who still has money. |
| D6 | Asked when the composer opens and again when a turn ends (when money was just spent). The server asks the class at most once per person per 30 s. |
| D7 | The class raising, or not answering within 3 s ⇒ 「預算暫時查不到」 in muted text, logged; asked again after the next turn. |
| D8 | **The AI is not told.** The figure is for the person only — not in the prompt, not a tool. |
| D9 | Shown above the composer of **both** the item chat and the knowledge-base chat, one component. |

**Not asked, following convention:** only the person themselves — `GET
/me/llm-budget` answers for whoever asks, it takes no user id. UI per NN/g
*Visibility of system status*; a `warn` / `critical` tone is said in words as
well as colour. A purely opt-in knob ⇒ a line in `docs/configuration.md`, no
`migrations.md` entry (CLAUDE.md).

## Interface

```python
class BudgetLink(Struct):
    label: str
    url: str

class BudgetStatus(Struct, kw_only=True):
    remaining: float | None = None
    total: float | None = None
    currency: str = "USD"
    message: str = ""
    link: BudgetLink | None = None
    tone: Literal["info", "warn", "critical"] = "info"

class ILlmBudget(abc.ABC):
    @abc.abstractmethod
    async def budget_for(self, request: Request, *, user_id: str) -> BudgetStatus | None: ...
```

## Phases

- **P1 — the seam.** `ILlmBudget` + `BudgetStatus` in `api/llm_budget.py`;
  `server.llm_budget` in config; `factories.get_llm_budget` via
  `_construct_dotted`; wired in the composition root (so
  `tests/config/test_server_settings_are_wired` sees it read).
- **P2 — the route.** `GET /me/llm-budget` → `{status: "off" | "ok" |
  "unavailable", budget?: …}`: `off` with no class configured, `ok` with the
  class's answer (or none), `unavailable` when it raised or took over 3 s
  (logged). Per-person 30 s cache; concurrent asks for one person share one
  call. Pydantic response model.
- **P3 — the cell.** A `BudgetBar` in the item composer's `composer-status`
  row: 「AI 剩 $11／共 $20」 (formatted per `currency`), then the message and
  the link; tone as colour + words; muted 「預算暫時查不到」 when unavailable;
  nothing when off or `None`. A TanStack query (one key per person, not per
  item), refetched by `useChatSession`'s turn-end hook.
- **P4 — the knowledge-base chat.** The same row above the KB composer, the
  same component; refetched when the KB turn ends.
- **P5 — docs.** `docs/configuration.md` (the knob and a minimal example
  class); `docs/extending-the-platform.md` (the seam beside `IRequestEnv`);
  the plan's As built.

## Verification

- Targeted tests per phase, the four gates, the FE typecheck; mutation-probed
  applications of each rule (empty config ⇒ no call; raise / timeout ⇒
  `unavailable`; the 30 s cache; remaining-first formatting; no `HTML` in the
  message; never in the prompt).
- A live check with a stand-in class (numbers, message-only, `None`, raising,
  slow) at two widths, item chat and KB chat; a /web-demo before review.

## As built

- **`unavailable` is kept for the same 30 s** as an answer (`BudgetCache`):
  D7's "asked again after the next turn" holds once the window has passed — a
  cost system that is down is not asked again by every chat that opens
  meanwhile. Concurrent asks for one person share one call, shielded so one
  closed tab cancels nothing.
- **Only http(s) links are passed on** (`_safe_link`), others dropped and
  logged: the URL comes from the deploy's code and lands in an `<a href>`, where
  a `javascript:` one would run in the person's page. Not asked; a security
  default.
- **The tone is a word and a dot.** `warn` / `critical` put 「注意」 / 「警告」
  before the figure and a `--warn` / `--err` dot beside it; the text stays ink,
  because `--warn` on paper is too light to read at 11px.
- **Numbers are formatted with one set of symbols — en-US's — for every
  reader**: `$11` for USD (the user's example), `NT$11`, `€11`; cents only when
  there are any (`$3.50`). The reader's own locale was tried first: a TWD
  deploy then read `$11` in zh-TW, the same as USD (review round 1). An
  ISO-shaped code Intl does not know reads `XYZ 11`; one it refuses
  (`credits`, empty) reads as the rounded number and the code.
- **The budget is one cell of the status row, and moves to the next line
  only when the row has no room** (the user: 「擺不下才換行」). Recording the
  demo, a wrapped line began with a dangling 「·」 — the row drew a dot before
  every cell but the first (`* + *::before`), so whichever cell wrapped led
  with one; `· compact` did it before this feature too. Now every cell carries
  its dot in a fixed 16px slot, the row is pulled left by that slot and the slot
  column clipped (`clip-path`), so a line-starting cell's dot is cut off and
  every other shows — measured in a real browser at two widths: every line's
  first cell at offset 0, every other at 16px or more. The link is underlined.
- **Asked once more after a turn, when the server's window ends.** A turn that
  ends inside a person's 30 s window is answered from the cache — the figure
  from before the turn. The answer carries `stale_in_s` (seconds until the
  server asks the class again); after each turn end the page asks once more
  then (`lib/llmBudget.refreshBudgetAfterTurn`, used by both chats). No polling
  beyond that one ask; a later turn replaces it.
- **The 30 s window is per API pod** — the cache is in process memory, so with N
  pods the class can be asked up to N times per person per window.
- **The knowledge-base chat refreshes from its own turn end** (`useKbChat`):
  it does not run on `useChatSession`. Missed in the first build; review
  round 1.
- **A link with no label is dropped** like a non-http one: it would draw as
  nothing, focusable and nameless.
- **`BudgetStatus` / `BudgetLink` are keyword-only and frozen** (the Interface
  above shows plain `Struct`): build them with field names.
- **The example class lives in `docs/extending-the-platform.md`**, beside
  `IRequestEnv`; `docs/configuration.md` has the knob's row and links there.
- **The `docs/configuration.md` line landed in P1**: `tests/config/
  test_server_settings_are_documented` requires every `server.*` key to be
  named in the manual the day it exists.
- **The knowledge-base row** is a `composer-status` row of its own above
  `kb-composer`, hidden by CSS when empty.

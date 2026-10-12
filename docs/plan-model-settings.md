# Plan — edit model settings from the web (superuser)

## Problem

Which presets exist, which model and endpoint each uses, which presets an App's
picker offers, and which preset the platform's own jobs use (KB retrieval, card
drafting, wiki, VLM, judge) are all in `config.yaml` and each App's `app.json`.
They are read once at boot — the failover chains (`factories.get_runner`), the
KB / card / wiki / VLM / judge LLM objects, the `AppCatalog`, and each of the
nine worker processes hold a boot snapshot, and nothing reloads. So adding a
model, pointing a preset at another one, or changing what a picker lists means
editing files and redeploying.

## Decisions

Source of each: **user** = chosen in the grill of 2026-10-12; **mine** =
settled by me, listed for the user to overturn.

| # | Decision | Source |
|---|---|---|
| M1 | A **superuser-only** "model settings" page. Everyone else still only picks from the pickers they are given | user |
| M2 | **Overlay**: `config.yaml` / `app.json` stay the deploy defaults; what the page changes is stored in the database and **wins** over them. New presets can be added; every changed value can be reset to the default. (Same shape as the per-user quota override, `quota/user_limits.py`.) | user |
| M3 | Scope: **presets**, **whole `app.json`** of each App, and the **platform's own model choices** (`kb.retrieval_llm`, `card_drafter`, `vlm_llm`, `wiki.llm`, `health.judge_llm`, sub-agent models) | user |
| M4 | **Draft, then Apply.** Edits are saved as a draft and change nothing running. Apply validates and publishes the draft as a new version identified by a string (the content hash); every user of the settings compares that string before each use and reloads only when it differs | user |
| M5 | **History and one-click rollback.** Each Apply is a version: who, when, the diff from the previous one. Rollback = apply an earlier version again | user |
| M6 | **API keys can be entered on the page**, write-only: shown as set / unset with a short suffix, never returned, never in history or diffs (history records that a key changed) *As built:* a key under 16 characters shows only that it is set — the end of a short value is most of it (every preset `env` value is a key, M7); the threshold is mine (review r2) | user |
| M7 | What is secret is decided by the **same function** the boot-time config dump uses (`config/dump.py:_is_secret`); its known gaps are fixed there, once, for both: `_token` matched case-insensitively, and every value of a preset's `env` | user ("用同一套就好") |
| M8 | Apply runs **structure checks only** (a default preset that exists, fallbacks that resolve, required fields — the loader's own checks) and refuses on failure. A separate **"test connection"** button per preset makes one minimal call and shows the result; it never blocks Apply — a provider outage must not stop a rollback | user |
| M9 | The editor is **text** (YAML/JSON) checked against the same schema as Apply, with the error's line and reason shown as you type, a diff before Apply, and each value's source (deploy default / changed here) with reset | user |
| M10 | **Profiles** (`presets`, `default_preset`) are in scope too | mine — they name presets; editing presets without them is half an edit |
| M11 | The database stores a **sparse patch**: only the values that differ from the default. A later `config.yaml` change to a value the page never touched still takes effect; where both changed, the page wins | mine |
| M12 | "Deleting" a preset that comes from `config.yaml` **hides** it in the overlay; reset brings it back | mine |
| M13 | **One function** produces the effective settings (defaults ⊕ published patch), used by the API, all nine workers and `skill_eval` — no consumer merges on its own | mine |
| M14 | Expensive objects (failover chains, LLM clients with their connection pools) are cached by a **fingerprint of their resolved settings** and rebuilt only when it changes; if the version cannot be read, the last good settings stay in use | mine |
| M15 | The page sits under a superuser-only "Settings" entry; its layout follows NN/g and Material 3, cited in the PR | mine |
| M16 | A process reads the version string **once a second on a thread of its own** (`LiveSettings.start`, started by the API's and every worker's `main`); a request or a job only reads memory. An Apply is in force everywhere within about a second. Without the thread (tests, `skill_eval`) a use reads the version itself at most once a second (`VERSION_TTL_S`). P10 is why: one read measured ~17 ms on Postgres, and the request that crossed the window paid it on the event loop | mine — found in P4, changed by P10 |
| M17 | **Turning a role on or off is not done here.** Whether a role (`kb.retrieval_llm`, `card_drafter`, `health.judge_llm`, …) is on decided at boot which features were wired (the quality judge, the drafter, the goal checker, code-wiki), and boot reads `config.yaml`, not the page — so Apply refuses a change to whether a role is on, with "change it in config.yaml" (`ROLES` in `model_settings/effective.py`). Changing **which** model a role uses is live | mine — found in P5 |
| M18 | **The page edits only what a running process reads again.** The config part is limited to the model choices of M3 (`LIVE_CONFIG` in `model_settings/effective.py`: `llm`, everything under `agents` (presets, `subagent_models`, the usage lists such as `kb_chat`), the role references); any other config key is refused at Apply with "change it in config.yaml". Storing a value that does nothing until a restart is a dead knob. The same holds inside a live part: the `infer_modules` run knobs (`enhancements`, `reasoning_effort`, `parallelism`, `collection`) and an App's `resources` / `slug` are read at boot and held, so a change to them is refused. Apply also runs the boot's workflow check (`validate_workflow_profiles`) on the draft, so a workflow without its `run.py` is refused rather than found at its first run. A patch can change an App or profile that ships and cannot create one: a patch-only App or profile would pass Apply and never be listed or used (review r1), so it is refused too | mine — found in P5 |
| M19 | `null` **hides a preset and means nothing else** (M12). Anywhere else — in the config part or under `apps` / `profiles` — it would delete a key the loader or the App needs; a value goes back to its default by leaving the patch, which is what reset does | mine — found in P5 |
| M20 | A **key** set or cleared on the page takes effect at the next **Apply**, like every other change: it is part of the version string (its revision, never its value), so it reaches the processes the same way and history records that it changed. A key is **staged** until then (`_SettingsKey`) and Apply copies it to the **applied** record (`_SettingsAppliedKey`) every process builds from — review r1 found a process that started after a key change built with the staged value, a key nobody applied. A rollback brings back an earlier version's values and leaves the keys as they are in force (`publish(..., apply_keys=False)`): a staged key stays staged — review r2 found rollback applying it while its dialog said keys stay | mine — found in P2, fixed by reviews r1 and r2 |

### Sources for the page (M15)

Checked against screenshots of the built page in real Chromium at 1280, 768
and 390 px during the PR (CLAUDE.md "UI work must follow published UI/UX
guidance"). Most are also cited in `web/src/pages/ModelSettingsPage.tsx` beside
what they shaped; the ellipsis and the forms-error article are cited only here.

- Three peer sections as a tab strip, two cues on the selected tab — NN/g,
  "Tabs, Used Right" (https://www.nngroup.com/articles/tabs-used-right/);
  Material 3, Tabs (https://m3.material.io/components/tabs/guidelines).
- Apply / roll back / clear behind a confirmation that names the outcome and
  lists what changes — NN/g, "Confirmation Dialogs Can Prevent User Errors"
  (https://www.nngroup.com/articles/confirmation-dialog/).
- Apply offered only when it would change something, the status saying which —
  NN/g, "10 Usability Heuristics", #1 visibility of system status
  (https://www.nngroup.com/articles/ten-usability-heuristics/).
- Problems drawn on the editor line they name, a summary under the editor,
  announced politely — NN/g, "Error-Message Guidelines"
  (https://www.nngroup.com/articles/error-message-guidelines/); "10 Design
  Guidelines for Reporting Errors in Forms"
  (https://www.nngroup.com/articles/errors-forms-design-guidelines/).
- Title, description and what is in force as one block — NN/g, "Proximity
  Principle in Visual Design" (https://www.nngroup.com/articles/gestalt-proximity/).
- A reset that looks clickable (outlined, not text-only) — NN/g, "Beyond Blue
  Links" (https://www.nngroup.com/articles/clickable-elements/); Material 3,
  Buttons (https://m3.material.io/components/buttons/guidelines).
- "Apply…" with an ellipsis because it asks for confirmation — Microsoft,
  "Command Buttons" (https://learn.microsoft.com/en-us/windows/win32/uxguide/ctrl-command-buttons).
- A visible label on the key path field, not a placeholder — NN/g,
  "Placeholders in Form Fields Are Harmful"
  (https://www.nngroup.com/articles/form-design-placeholders/).

### Known costs

- Keys are stored in the database in clear, as the existing Private env values
  are; only superusers can set them and no one can read them back.
- The Diagnostics checks (`get_check_registry`: one probe per App's default
  agent, the KB / wiki probes) are built at boot and probe the boot settings
  until a restart. They are diagnostics, not the turn path.
- The bulk of the work is turning every boot snapshot (API and nine workers)
  into "read when used".

### Performance (to be measured, not assumed — P10)

*Measured 2026-10-12* (local Postgres 16 in docker for the meta / resource rows,
blobs on disk; and the disk backend alone — the production image ships no
Postgres driver, so disk is likely what production runs):

| | disk | Postgres |
|---|---|---|
| read the version (one row) | 0.16 ms | 16.6 ms (max 49) |
| a use of the settings, version unchanged | 0.3 µs | 0.5 µs |
| first use after an Apply: read + rebuild | 2.7 ms (max 46) | 41 ms (max 94) |
| `load_app_manifest`, same version | 1.4 µs | 2.7 µs |
| `load_app_manifest`, first read after an Apply | 0.14 ms | 0.27 ms |
| the checks Apply runs (`problems`) | 7 ms (first 3 s: imports) | 6 ms |

The Postgres read is specstar's `get`, not a bare row; paid by whichever request
crossed the one-second window, synchronously, it was a ~17 ms stall of the event
loop every second. So the read moved to a thread (M16): a request now pays the
0.5 µs, and the rebuild happens off the request path too.

Each use reads one small "current version" value; only a change reloads the
patch. Merging is microseconds; a turn is seconds of model time. The two ways
this could be slow are both ruled out by M14: rebuilding clients per turn (loses
connection reuse) and waiting on the database (the last good version is used).

## Phases

Each `/tdd`, one commit; the review lenses, then CI, as usual.

- **P1 — the secret rule.** `_is_secret`: `_token` case-insensitive; a preset's
  `env` values always secret. Boot dump and page share it (M7).
- **P2 — storage.** Specstar models registered after `spec.apply` (no auto-CRUD,
  like `_UserQuota`): the draft patch, published versions (hash, author, time,
  patch, previous), the current-version pointer, and the write-only key store.
- **P3 — the effective settings.** One function: defaults ⊕ published patch for
  presets, every App's `app.json`, profiles and the platform's model choices
  (M3, M10–M13); hidden presets (M12); validation returning every error with
  its path (M8). *As built:* app.json and profile errors are all returned; the
  config part reports the loader's **first** error only, because the loader
  raises on the first problem it finds — the same refusal a boot gives.
- **P4 — version check and caches.** The cheap current-version read; a process
  cache keyed by version; fingerprinted failover chains and LLM clients; last
  good kept on read failure (M4, M14).
- **P5 — the API consumers.** `AppCatalog.resolve`, `get_runner`, the KB /
  card / wiki / VLM / judge / goal-checker LLMs, sub-agent models, KB chat's
  agent list — each reads through P3/P4.
- **P6 — the workers and `skill_eval`.** The worker composition
  (`worker/__main__.py`, the `build_app` workers) and `skill_eval` read the same
  way, so a job after Apply uses the new settings.
- **P7 — the routes.** Superuser-only: effective settings with each value's
  source and secrets masked; save draft; validate; diff; apply; history;
  rollback; set / clear a key; test connection.
- **P8 — the page.** The text editor, inline errors, diff, sources and reset,
  Apply, history and rollback, keys, test connection; reachable only for
  superusers.
- **P9 — docs.** `docs/configuration.md`, `docs/glossary.md` (`AgentConfig`
  "not in the DB" no longer holds for overrides), `docs/migrations.md` entry
  (new models; behaviour change with no knob).
- **P10 — measure.** The per-turn and per-job cost of the version check and of
  a reload after Apply, on a real backend.

## Open risks

- An Apply takes effect per model CALL, not per job: a call already under way
  finishes on the old settings, and every call after it — including the next
  step of a job already running (a wiki pass, a card-gen run) — uses the new
  ones, so one job can span two versions. (Review r1 caught the earlier
  sentence, "finishes that job on the old settings", as untrue.)
- (superseded) A worker that is mid-job when Apply lands finishes that job on the old
  settings; the next job reads the new version. Stated in the page's copy.
- Validation at Apply covers structure only (M8); a wrong model name passes it
  and fails on first use — the test-connection button is how to find out first.

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
| M7 | What is secret is decided by the **same function** the boot-time config dump uses (`config/dump.py:is_secret_at`, decided on the walked keys — final review 9; `is_secret` was `_is_secret` before P1 and now only splits a joined path for it); its known gaps are fixed there, once, for both: `_token` matched case-insensitively, and every value of a preset's `env` | user ("用同一套就好") |
| M8 | Apply runs **structure checks only** (a default preset that exists, fallbacks that resolve, required fields — the loader's own checks) and refuses on failure. A separate **"test connection"** button per preset makes one minimal call and shows the result; it never blocks Apply — a provider outage must not stop a rollback | user |
| M9 | The editor is **text** (YAML/JSON) checked against the same schema as Apply, with the error's line and reason shown as you type, a diff before Apply, and each value's source (deploy default / changed here) with reset. *As built:* the text is **YAML**, as `config.yaml` is (JSON, being YAML, is still read); it was first built as JSON only, which narrowed this decision (user, 2026-10-13) | user |
| M10 | **Profiles** (`presets`, `default_preset`) are in scope too | mine — they name presets; editing presets without them is half an edit |
| M11 | The database stores a **sparse patch**: only the values that differ from the default. A later `config.yaml` change to a value the page never touched still takes effect; where both changed, the page wins | mine |
| M12 | "Deleting" a preset that comes from `config.yaml` **hides** it in the overlay; reset brings it back | mine |
| M13 | **One function** produces the effective settings (defaults ⊕ published patch), used by the API, all nine workers and `skill_eval` — no consumer merges on its own | mine |
| M14 | Expensive objects (failover chains, LLM clients with their connection pools) are cached by a **fingerprint of their resolved settings** and rebuilt only when it changes; if the version cannot be read, the last good settings stay in use | mine |
| M15 | The page sits under a superuser-only "Settings" entry; its layout follows NN/g and Material 3, cited in the PR *As built:* a direct "模型設定" item in the superuser part of the platform menu (there is no separate "Settings" entry), at `/settings/models` | mine |
| M16 | A process reads the version string **once a second on a thread of its own** (`LiveSettings.start`, started by the API's and every worker's `main`); a request or a job only reads memory. An Apply is in force everywhere within about a second. Without the thread (tests, `skill_eval`) a use reads the version itself at most once a second (`VERSION_TTL_S`). P10 is why: one read measured ~17 ms on Postgres, and the request that crossed the window paid it on the event loop | mine — found in P4, changed by P10 |
| M17 | **Turning a role on or off is not done here.** Whether a role (`kb.retrieval_llm`, `card_drafter`, `health.judge_llm`, …) is on decided at boot which features were wired (the quality judge, the drafter, the goal checker, code-wiki), and boot reads `config.yaml`, not the page — so Apply refuses a change to whether a role is on, with "change it in config.yaml" (`ROLES` in `model_settings/effective.py`). Changing **which** model a role uses is live | mine — found in P5 |
| M18 | **The page edits only what a running process reads again.** The config part is limited to the model choices of M3 (`LIVE_CONFIG` in `model_settings/effective.py`: `llm`, everything under `agents` (presets, `subagent_models`, the usage lists such as `kb_chat`), the role references); any other config key is refused at Apply with "change it in config.yaml". Storing a value that does nothing until a restart is a dead knob. The same holds inside a live part: the `infer_modules` run knobs (`enhancements`, `reasoning_effort`, `parallelism`, `collection`) and an App's `resources` / `slug` are read at boot and held, so a change to them is refused. Apply also runs the boot's workflow check (`validate_workflow_profiles`) on the draft, so a workflow without its `run.py` is refused rather than found at its first run. A patch can change an App or profile that ships and cannot create one: a patch-only App or profile would pass Apply and never be listed or used (review r1), so it is refused too. The page's text is taken literally — the loader never `${…}`-expands it, and `${` in the config part is refused: expanding read any server environment variable back to whoever typed its name (final review); in App text `${` is plain text and allowed (final review 3). A usage list the running catalog cannot build (an entry whose preset has no `prompt_file`) is refused too: a boot refuses it, and live it failed every use of the catalog (final review 3). `kb.quality_judge` was validated by the loader and never built — a dead knob from config.yaml as much as from the page — so the loader builds it now rather than the page refusing it (final review 4; runbook #928). | mine — found in P5 |
| M19 | `null` **hides a preset and means nothing else** (M12). Anywhere else — in the config part or under `apps` / `profiles` — it would delete a key the loader or the App needs; a value goes back to its default by leaving the patch, which is what reset does | mine — found in P5 |
| M20 | A **key** set or cleared on the page takes effect at the next **Apply**, like every other change: it is part of the version string (its revision, never its value), so it reaches the processes the same way and history records that it changed. A key is **staged** until then (`_SettingsKey`) and Apply copies it to the **applied** record (`_SettingsAppliedKey`) every process builds from — review r1 found a process that started after a key change built with the staged value, a key nobody applied. A rollback brings back an earlier version's values and leaves the keys as they are in force (`publish(..., apply_keys=False)`): a staged key stays staged — review r2 found rollback applying it while its dialog said keys stay | mine — found in P2, fixed by reviews r1 and r2 |
| M21 | **A deploy key goes only where config.yaml sends it.** A key config.yaml sets (often `${…}` from the environment) can be read by no one; Apply and test connection refuse an endpoint (`base_url`, a preset's chain, a role's reference) or a usage entry (`agents.kb_chat` and the like, resolved as the running catalog resolves it — final review 3) that would send such a key somewhere config.yaml never pairs it with — where a call goes being the provider litellm resolves from the model, the base_url, and a URL the model string itself carries (final review 7: `huggingface/https://…` is its own address, so pairing on base_url alone let the key through) — set a key for the new endpoint on the API keys tab instead. A key set on the page may go anywhere: its setter knows it. Likewise an App's files stay inside its folder: `prompt_file` must resolve under `apps/<slug>/`, and a workflow id is a plain name (it is a path). On the config side a `prompt_file` the page writes — anywhere: a preset, a usage entry — is a packaged prompt (`pkg:…`) or one config.yaml already names; any other path is a file on the server read into a system prompt a chat can repeat back, config.yaml itself included (final review 5: `/proc/self/environ` passed Apply). Apply also sends the revision of every staged key it listed, so a key set or cleared since is a 409 — paths alone let a clear through that the dialog listed as a set (final review 5) | mine — final review (a changed `base_url` sent the deploy's key to any server; `prompt_file` read `/proc/self/environ`; a `../` workflow id could load any `run.py`) |
| M22 | **A key is set by picking where it goes, never by typing a path.** The API keys tab lists every place a key can sit — the default `llm`, each preset, each role reference, each `env` name a preset has — with its status (not set / `…4321` / waiting for Apply) and Set / Clear on the row; Set opens a masked field in that row. Only a new `env` name is typed (the one thing the system cannot list): pick a preset, name the variable, give the value. The server computes the list (`key_slots`) from the saved draft, so it always names real places. NN/g heuristic #6, recognition rather than recall (https://www.nngroup.com/articles/recognition-and-recall/): typing `config.agents.presets.kb-default.llm.api_key` was recall | user ("金鑰設定方式有點反直覺 — 要手打設定路徑") |
| M23 | **The editor holds the whole current settings, and the page works out what changed.** It opens on the deploy defaults with the saved draft over them — what the page edits, as YAML, the way `config.yaml` is read — and the person edits it in place; on save the page diffs it against the defaults and stores only what differs (M11 unchanged; the patch is never shown). Anything deleted or left out goes back to its default — never hidden: a pasted fragment that holds one preset must not hide the others; a preset is hidden by writing `null` for it (M12). A secret shows as its mask and an unchanged mask — the page's or the boot dump's `*** set (N chars) ***` — is no change; a secret typed in clear is still refused (M6). What can be changed can be copied and pasted (user: "有些不能改我懂 但能改的要能複製貼上才對"): the diff's baseline is the whole boot config, so a pasted resolved config's sections the page does not edit drop out when unchanged, and a changed one is refused with "change it in config.yaml". Supersedes the patch-as-text editor of M9's first build: writing only the differences was the system's concept, not the person's (NN/g heuristic #2: "speak the users' language … concepts familiar to the user" rather than "system-oriented terms" — https://www.nngroup.com/articles/match-system-real-world/). *After review 17:* every `null` the draft holds is shown where it was written, not only a preset's hide — a null the check refuses (outside a preset) stayed in the draft while it vanished from the text; and a list (stored whole) pasted back with its secrets masked the other way — the page's `*** set ***`, the dump's `*** set (N chars) via ${X} ***` — is no change. *After review 18:* lists are compared in order everywhere on the page (`samePatch`: what is stored, what the page calls a change, whether there are unsaved edits) — a preset's `fallbacks` is its failover chain and the other lists are display order; compared as sets, a reorder was stored but said nothing, so it could be saved and never applied. *After review 19:* unsaved edits are measured against what the editor opened with — the saved draft as its text reads back, where a value equal to its default drops out — not the raw draft: once config.yaml took the page's value (or a rollback brought back a patch made under other defaults) the page opened "unsaved" and Discard could not clear it | user ("那這個會超級難用" → 編輯完整的現行設定) |
| M24 | **Test connection stops at the model's first token.** The first token — its thinking included — already shows the endpoint answers, the key is taken and the model exists; the page says how long the model took to start answering, and hangs up so the provider stops generating — closing the response litellm wraps, since litellm's sync stream wrapper has no `close()` of its own; checked against a fake provider streaming 40 chunks: an OpenAI-compatible endpoint and an Ollama one both see the client hang up at chunk 2 (before review 17's fix the OpenAI-compatible one sent all 40). Measured on the page's own path (litellm → ollama `qwen3:14b`, CPU only): waiting for the whole reply took 34–43 s, ~100 tokens of thinking before a two-letter "OK" at ~4 tokens/s; the first token takes 1.2–2.0 s with the model loaded and 17.9 s when ollama must load it first. A stream that ends with nothing in it is a failure. The result is said in seconds, like the running line above it. It waits up to 120 s for that first token: nothing streams before a model is loaded, and in the demo a CPU model read back from disk took over 30 s — the old 30 s bound failed it twice; a longer bound costs only when the endpoint never answers. NN/g "Response Times: The 3 Important Limits" — past ~10 s the user's attention is lost (https://www.nngroup.com/articles/response-times-3-important-limits/) | user asked why it took so long ("你怎麼測試？為什麼那麼久"); this answer proposed it, then "928繼續做完" |

### Sources for the page (M15)

Checked against screenshots of the built page in real Chromium at 1280, 768
and 390 px during the PR (CLAUDE.md "UI work must follow published UI/UX
guidance"). Most are also cited in `web/src/pages/ModelSettingsPage.tsx` beside
what they shaped; the ellipsis is cited only here.

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
- A test call that can take tens of seconds says it is running and for how
  long, under a button that keeps its label — NN/g, "Progress Indicators Make
  a Slow System Less Insufferable" (https://www.nngroup.com/articles/progress-indicators/).
- The editor bounded by the window and scrolled inside, so Save and Apply stay
  in reach under ~1000 lines of settings (M23; review 17 measured an 18,016 px
  editor), and the Save / Apply row sticky at the bottom of the view, since
  what sits above the editor is taller in English than in Chinese (review 19)
  — NN/g, "Scrolling and Attention"
  (https://www.nngroup.com/articles/scrolling-and-attention/).
- Each key form says its own refusal under itself and keeps what was typed — a
  row's field, and the form for a new env name alike; a variable name says what
  it may hold under the field, and why one cannot be used as soon as it is
  typed — NN/g, "10 Design Guidelines for Reporting Errors in Forms" (above);
  the fields top-aligned so a hint under one does not lift it above the rest —
  Material 3, Text fields (https://m3.material.io/components/text-fields/guidelines).
- The keys tab's "waiting for Apply" line and its Apply sit above the list of
  places, not under it: the list holds every place a key can go (M22) and the
  Apply below it was off screen once a key was set — NN/g, "Scrolling and
  Attention" (above).
- One unit for one quantity: Test connection's running line and its result
  both in seconds — NN/g, "Consistency and Standards"
  (https://www.nngroup.com/articles/consistency-and-standards/).
- Every place a key can go listed by name, each with its status and Set /
  Clear on its row — no path to type (M22) — NN/g, "Memory Recognition and
  Recall in User Interfaces" (https://www.nngroup.com/articles/recognition-and-recall/):
  "making information and interface functions visible"; the classic case is a
  menu, where "the computer shows you the available commands, and you
  recognize the one you want".
- The key field opens in the row it is for, with a visible label rather than a
  placeholder, and a refusal is drawn under that field — NN/g, "Placeholders in
  Form Fields Are Harmful" (https://www.nngroup.com/articles/form-design-placeholders/),
  "10 Design Guidelines for Reporting Errors in Forms"
  (https://www.nngroup.com/articles/errors-forms-design-guidelines/).

### Known costs

- A usage entry's own `llm.api_key` set in `config.yaml` (e.g. on
  `agents.kb_chat[0]`) cannot be kept through an edit of that list here: a
  merge patch replaces a list whole, the page will not take the secret as text
  (M6), and a key cannot sit inside a list (only at a text setting). Edit such
  a list in `config.yaml`; a preset's key is the place the page manages.
- A usage entry's `env` values are not secret by their place, on this
  superuser-only page as in the boot dump (M7: one rule for both): a key cannot
  sit inside a list, so treating them as secret left no way to keep one through
  an edit of that list. One whose NAME is a secret's (any `*_token`, in
  either case — M7's leaf-name rule) is still secret, and so is the list trap above: such a list is edited in
  `config.yaml`. A secret for a usage entry belongs in its preset's `env`.

- M21 guards the keys config.yaml sets. A preset with no `api_key` there
  calls with whatever its provider's client reads from the server's
  environment (`OPENAI_API_KEY`, the shape `deployment.md` documents), and
  moving such a preset's `base_url` sends that key along. Whether the
  environment holds a key cannot be told reliably: final review 8 read
  litellm's `validate_environment` as "the server holds a key", but its
  "nothing missing" is also true for a keyless vLLM and for Ollama's
  `OLLAMA_API_BASE` (an address), so moving those was refused with a key that
  did not exist (final review 9) — reverted. The remedy is the operator's and
  exact: name the key in config.yaml (`llm.api_key: ${OPENAI_API_KEY}`), and M21
  guards it like any other (runbook #928).
- Unsaved edits are kept in the browser's `sessionStorage` until saved or
  discarded, so leaving the page and coming back gives them back (the router
  cannot hold a navigation to ask); a secret typed into them stays in that
  browser tab's session, where the person typed it.
- Keys are stored in the database in clear, as the existing Private env values
  are; only superusers can set them and no one can read them back.
- A Diagnostics turn replay (`get_replay_service`) builds its endpoints from
  the boot settings too, until a restart.
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

- **P1 — the secret rule.** `is_secret` (was `_is_secret`): `_token` case-insensitive; a preset's
  `env` values always secret. Boot dump and page share it (M7) — as
  `is_secret_at` on the walked keys since final review 9.
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

- An Apply takes effect per model call, not per job: a call already under way
  finishes on the old settings, and every call after it — including the next
  step of a job already running (a wiki pass, a card-gen run) — uses the new
  ones, so one job can span two versions. An agent turn keeps the model it
  started with (its agent config is resolved once), but each attempt within it
  — a retry after a rate-limit hold, a repair, the #624 shrink — reads the
  endpoint and failover chains afresh, so a turn under way can finish on the
  new endpoint. (Review r1 caught "finishes that job on the old settings" as
  untrue; the final review caught "per call" as untrue for an agent turn's
  model; final review 4 caught "the turn finishes on the old settings" as
  untrue for its endpoint.)
- (superseded) A worker that is mid-job when Apply lands finishes that job on the old
  settings; the next job reads the new version. Stated in the page's copy.
- Validation at Apply covers structure only (M8); a wrong model name passes it
  and fails on first use — the test-connection button is how to find out first.

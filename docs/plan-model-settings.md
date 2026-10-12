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
| M6 | **API keys can be entered on the page**, write-only: shown as set / unset with a short suffix, never returned, never in history or diffs (history records that a key changed) | user |
| M7 | What is secret is decided by the **same function** the boot-time config dump uses (`config/dump.py:_is_secret`); its known gaps are fixed there, once, for both: `_token` matched case-insensitively, and every value of a preset's `env` | user ("用同一套就好") |
| M8 | Apply runs **structure checks only** (a default preset that exists, fallbacks that resolve, required fields — the loader's own checks) and refuses on failure. A separate **"test connection"** button per preset makes one minimal call and shows the result; it never blocks Apply — a provider outage must not stop a rollback | user |
| M9 | The editor is **text** (YAML/JSON) checked against the same schema as Apply, with the error's line and reason shown as you type, a diff before Apply, and each value's source (deploy default / changed here) with reset | user |
| M10 | **Profiles** (`presets`, `default_preset`) are in scope too | mine — they name presets; editing presets without them is half an edit |
| M11 | The database stores a **sparse patch**: only the values that differ from the default. A later `config.yaml` change to a value the page never touched still takes effect; where both changed, the page wins | mine |
| M12 | "Deleting" a preset that comes from `config.yaml` **hides** it in the overlay; reset brings it back | mine |
| M13 | **One function** produces the effective settings (defaults ⊕ published patch), used by the API, all nine workers and `skill_eval` — no consumer merges on its own | mine |
| M14 | Expensive objects (failover chains, LLM clients with their connection pools) are cached by a **fingerprint of their resolved settings** and rebuilt only when it changes; if the version cannot be read, the last good settings stay in use | mine |
| M15 | The page sits under a superuser-only "Settings" entry; its layout follows NN/g and Material 3, cited in the PR | mine |

### Known costs

- Keys are stored in the database in clear, as the existing Private env values
  are; only superusers can set them and no one can read them back.
- The bulk of the work is turning every boot snapshot (API and nine workers)
  into "read when used".

### Performance (to be measured, not assumed — P10)

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
  its path (M8).
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

- A worker that is mid-job when Apply lands finishes that job on the old
  settings; the next job reads the new version. Stated in the page's copy.
- Validation at Apply covers structure only (M8); a wrong model name passes it
  and fails on first use — the test-connection button is how to find out first.

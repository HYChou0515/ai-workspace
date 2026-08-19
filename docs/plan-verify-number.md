# Plan — `verify-number`: a number you can check

## Goal

When someone asks the agent for a value computed from a table — a control
limit, a threshold, a rate, a yield — **they cannot verify the value itself**.
If they could, they would not have asked. `-20.14` and `-20.41` read the same.

So trust cannot come from reading the answer. It has to come from artifacts
that are cheaper to check than the computation, coupled tightly to it, and
loud when it is wrong.

`verify-number` is a shared skill that makes the agent produce those artifacts
alongside the number.

## What it is defending against

| class | example | what catches it |
|---|---|---|
| loud runtime error | traceback, exit 1 | already visible; a retry is fine |
| **silent** runtime error | `"1,204"` loads as text; `.mean()` drops nulls; a merge yields 0 rows | data assertions declared **before** the arithmetic |
| impl ≠ intention | `std()` where variance was meant; a rolling window where global was meant | metamorphic transforms — they need no ground truth |
| intention ≠ requirement | global σ where SPC within-subgroup σ (R̄/d2) was meant | the assumption list, ranked by how far each flip moves the answer |
| **statistic ≠ what it is taken to mean** | mean+7σ on a heavy tail: nominally 1-in-10¹², actually 1-in-400 | the answer drawn on the distribution, with the exceedance count |

The last row is the one no test can red-flag: code correct, intention correct,
requirement correct, number meaningless for the decision.

Nothing in the system covers any of this today. A scan of
`src/workspace_app/apps/**/*.md` and `src/workspace_app/kb/prompts/*.md` finds
no rule about computational correctness. The closest three are about other
things: `apps/rca/prompts/system.md:17` (a report's findings must carry numbers
and a chart), `apps/rca/profiles/local-lab/_prompt.md` (do not hallucinate a
data source; do not order findings by score), `apps/_sandbox.md:20` ("judge code
by running it" — about syntax errors, not about results).

## Shape

One `SKILL.md`, no `scripts/`. `sample-skills/verify-number/`, registered in
`apps/shared_skills.py`, opted into by `apps/rca/app.json` `agent.skills`.

Two constraints from the runtime drove the design:

- **`apps/_base.md:9` — "One tool call per response."** A six-step procedure is
  8–12 round trips, which a local model does not finish. The discipline is
  therefore ONE self-checking script: `write_file`, `exec`, `show_file`.
- **An agent's own matplotlib PNG is not shown to the user.** Only tool-package
  output is auto-declared as `[shown-files]`
  (`tooling/registry.py:347-370`); `show_file` (`agent/tools.py:226`) is taught
  nowhere but its own docstring, and `sci-plot` has no histogram in its catalog
  (`box_scatter` / `wafermap` / `defectmap` / `grouped_line`). The skill has to
  name `show_file` explicitly or step 4 fails silently.

Smaller ones, folded in: the sandbox `python-stack` has **no pandera**, so
assertions are plain `assert` + print; `_format_exec` (`agent/tools.py:56-77`)
**discards stderr on success**, so the report goes to stdout; `exec` output is
capped at 30,000 chars (`config/schema.py:324`), so the report stays compact.

## Feasibility criteria

Written down before running, so the bar cannot move afterwards.

1. ~~The target model finishes the procedure; Opus succeeding does not count.~~
   **Superseded.** Not because it was failing, but because the platform is to be
   model-agnostic and the model assumed capable — which makes "which model can
   follow this" the wrong question, since it presumes the guarantee comes from
   the model. What replaces it: **a second party can tune the guidance for their
   own model and measure the result** (see "Tuning the guidance" below).
2. The three planted defects are caught, and are missed with the skill off.
   **Met** for the discipline — see Results, part 1.
3. **The clean control raises no alarm.** A checklist that always cries wolf
   gets switched off, so this row decides whether the thing is usable at all.
   **Met** — the control scenario is silent.

## Evaluation scenarios

They live in `sample-scenarios/verify-number/` — four declarative `*.json`
scenarios plus `make_data.py`, which regenerates the CSVs from a fixed seed.
They sit beside `sample-skills/` and `sample-tools/` rather than inside the skill
folder, because any file shipped next to `SKILL.md` is copied into every user's
workspace on first `read_skill` — eval fixtures do not belong there.

| # | file | prompt | expected |
|---|---|---|---|
| A | `silent_dtype.csv` | mean − 3σ of `thickness` | names the column as text, not a number |
| B | `within_vs_global.csv` | lower limit at mean − 3σ for `cd` | calls `ask_user`; names within-subgroup sigma |
| C | `heavy_tail.csv` | mean + 7σ of `leak_na` | reports the 13 exceedances against what 7σ implies |
| D | `control_clean.csv` | mean − 7σ of `vt_mv` | answers with a chart and asks **nothing** |

D is the row that decides whether this is usable in practice.

Ground truth, measured from the generated files — what a correct run has to
agree with:

| # | measured |
|---|---|
| A | `thickness` loads as `object`; first value `1,156` |
| B | global σ = 3.853, within-lot σ = 0.392 (**9.8×**); mean − 3σ = **35.36** global vs **45.75** within-lot |
| C | threshold 56.33; **13 of 3000** rows beyond (1 in 231) where normality implies 3.8e-09 |
| D | mean − 7σ = 365.55; the ddof flip moves it 0.0084 (2e-5 relative); 0 rows beyond; min 405.88 |

## Phases

**P1 — is the discipline sound?** ✅ Done, and it needed no model: a hand-written
reference implementation over the four fixtures, plus a mutation table measuring
each transform's discriminating power. Results below.

**P2 — the second-party tuning loop.** ✅ Done: `workspace_app.skill_eval`, the
scenarios as data, and the control arm. This is what "model-agnostic" cashes out
to — the platform ships guidance *and* the means to retune it.

**P3 — wiring.** ✅ Done: one line in `SHARED_SKILLS`, one name in
`rca/app.json`, turning the nine already-red tests in
`tests/apps/test_verify_number_optin.py` green.

**P4 — live check in the real app.** Outstanding. What it measures after the
hardening question is settled: whether the model invokes the discipline at all,
what its `compute` looks like, and whether it acts on the report — not whether it
can do the checking.

**P5 — promotion, decided from P4 data, not in advance.** Profile default, or
into `prompts/system.md`; or harden the mechanical parts into shipped `scripts/`
(`apps/skill_payload.py:44` already copies a whole folder, #589); or, only if
pinned dependencies turn out to be needed, a tool-package.

## P1 run conditions

Measured on the dev box, because they decide how strong a P1 verdict can be.

**Ollama here serves entirely from CPU.** An RTX 3080 (12 GiB) is present and
idle (`nvidia-smi`: 0 MiB used), `/dev/nvidia*` exist, but `/api/ps` reports
`size_vram: 0` and the runner carries no `--n-gpu-layers`. This is not a
capacity problem — `qwen3:14b` at `num_ctx=12288` is 10.65 GiB and fits.

Consequences, measured rather than assumed:

| | |
|---|---|
| `qwen3:14b`, one step | > 600 s (litellm's default timeout expired first) |
| `qwen3:8b`, one step | **193.7 s** for 3109 prompt + 647 completion tokens |
| hosted fallback | none — no provider key in the environment |

So the P1 gate runs `qwen3:8b` with qwen3's `/no_think` switch. Both choices
make the gate **strictly harsher** than the deployed `qwen3-local`
(`ollama_chat/qwen3:14b`, reasoning on): a smaller model with no reasoning
budget. Therefore **a pass is conclusive and a fail is not** — a fail has to be
re-run on `qwen3:14b` with reasoning before it kills the design.

The tool-calling protocol itself is confirmed working: the first step returned
a well-formed `list_files` call.

## Results — part 1: is the discipline sound?

This half needs no model. A reference `analysis.py`, written by hand to follow
the four parts literally, was run over all four fixtures. If a careful
implementation cannot separate the planted defects from the clean control, the
skill text is wrong and no model could rescue it.

| fixture | behaviour | rc |
|---|---|---|
| A silent dtype | stops in part 2: "`thickness` arrived as object, not a number (first value `'1,156'`)" | 2 |
| B within vs global | part 4 ranks `scope` (moved 10.3851) above `ddof` (moved 0.0048) and flags only the first | 0 |
| C heavy tail | "13 rows are beyond a threshold that should hold almost none" — against 3.84e-09 implied by normality | 0 |
| D control | **silent**: the one sensitivity row moved 0.0084, nothing flagged, nothing asked | 0 |

Criteria 2 and 3 hold for the discipline itself. D staying quiet is the result
that matters most — a checklist that always cries wolf gets switched off.

### The five transforms each earn their place

Part 3 passed on all four fixtures, which means those fixtures never exercised
its discriminating power — the very thing it exists for. So the reference
`compute` was mutated with four mistakes people actually make, and each
transform scored separately:

| planted mistake | ×2 | +100 | shuffled | duplicated | constant |
|---|---|---|---|---|---|
| *(correct)* | pass | pass | pass | pass | pass |
| `var()` where `std()` was meant | **caught** | pass | pass | pass | pass |
| standard **error** of the mean, not the deviation | pass | pass | pass | **caught** | pass |
| returned the offset, forgot to add the level | pass | **caught** | pass | pass | **caught** |
| computed on the first half of the rows | pass | pass | **caught** | **caught** | pass |

Every mutation is caught, no single transform catches all of them, and the
correct implementation raises nothing. The standout is standard-error-for-
standard-deviation — a classic real confusion that **only row duplication
catches**; scaling and shifting both wave it through. That is the transform
that looks most redundant on the page, and it is the only defence against that
bug.

Caveat, stated so it is not read as more than it is: the same author wrote the
discipline and this reference implementation. It establishes that the four parts
are implementable and discriminating. It says nothing about whether a model
writes them correctly — that is part 2, below.

## Results — part 2: can the model follow it?

Reframed, on the deciding constraint that the model is to be **assumed capable**
and the platform **model-agnostic**. "Which model can follow this" is then the
wrong question — it presumes the guarantee comes from the model. What is true
instead is narrower and more useful:

> A capable model still needs good guidance, and **good guidance is
> model-specific**. A body tuned against one model is not tuned against the next.

So the deliverable is not a verdict on one model. It is the ability for whoever
deploys this — a second party, with their own model and their own data — to edit
the guidance and see what changed. That is `workspace_app.skill_eval`.

## Tuning the guidance — `python -m workspace_app.skill_eval`

Dump the shipped prompt, edit it, feed it back, score it. The model is not a
flag: the turn comes from `AppCatalog.resolve`, so it is the deployment's own
model, endpoint and prompt. `--preset` picks another of the App picker's presets.

```
# 1. the shipped guidance, as a file you can edit
python -m workspace_app.skill_eval --dump-skill verify-number -o ./tune

# 2. score it against the scenarios, with the no-skill control beside it
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/verify-number --control -o ./tune/run-1

# 3. edit ./tune/SKILL.md, rerun into run-2, compare the two reports
```

`--skill` takes a registered name **or a path**, which is what makes step 3 a
loop rather than a fork of the repo.

**The control arm is the point.** A scenario the guidance passes *and the
no-skill control also passes* measured nothing, and the report says so by name
rather than counting it as a win.

**Scoring is deterministic, not an LLM judge.** "Did it call `ask_user`", "does
the answer name the dtype" have objective answers; a judge would add a second
thing needing calibration before the first could be trusted. A scenario declares
`must_call` / `must_not_call` / `must_mention` / `must_not_mention`, where a
phrase may be a list of alternatives so an expectation is not brittle about
wording. Scenarios live in `sample-scenarios/`, beside `sample-skills/` and
`sample-tools/` — **not** inside the skill folder, because any file there besides
`SKILL.md` gets copied into every user's workspace on first `read_skill`.

Every scenario requires `exec`: a number reached in the model's head is neither
reproducible nor checkable, so "never do arithmetic in-context" is the one rule
with a fully objective test.

### What the harness models, and what it does not

The prompt comes from the app's **own** `AppCatalog.resolve` — the same call a live
turn makes — so the guidance under test is the guidance a real turn receives,
including the `## Available skills` index that makes `read_skill` triggering
measurable at all. `exec` output is framed like `agent.tools._format_exec`
(exit-code header, stderr dropped on success, middle-truncated at
`exec.output_max_chars`), and only the first tool call of a reply runs
(`apps/_base.md:9`). It does **not** model specstar, the sandbox jail, SSE, the
workspace quota or tool authorisation — everything it skips makes the real app
more forgiving, not less, so a green run licenses a live check rather than
replacing one.

A drift guard pins the one string that is reproduced rather than imported: if the
Apply chip's wording in `apps/skills.py` changes, `test_applied_header_matches_production`
fails rather than letting the eval quietly test a prompt nobody sends.

## References

Metamorphic testing: Chen, Cheung & Yiu (1998), *Metamorphic testing: a new
approach for generating next test cases*. Property-based testing: Claessen &
Hughes (2000), *QuickCheck*. N-version programming: Avizienis (1985).
One-at-a-time sensitivity: Saltelli et al., *Global Sensitivity Analysis*.

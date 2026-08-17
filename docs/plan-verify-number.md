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

1. The target model finishes the procedure. **Opus succeeding does not count** —
   the bar is the weakest model this ships to (`qwen3-local`).
2. The three planted defects are caught, and are missed with the skill off.
3. **The clean control raises no alarm.** A checklist that always cries wolf
   gets switched off, so this row decides whether the thing is usable at all.

## Evaluation fixtures

Fixed seed, reproducible. Generate to a scratch dir and upload through the UI.

```python
# verify-number evaluation fixtures — deterministic
import numpy as np
import pandas as pd

rng = np.random.default_rng(20260817)

# A — silent dtype. Thousands separators make a numeric column arrive as text.
n = 1200
pd.DataFrame(
    {
        "wafer_id": [f"W{i:05d}" for i in range(n)],
        "thickness": [f"{v:,.0f}" for v in rng.normal(1200, 45, n)],
    }
).to_csv("a_silent_dtype.csv", index=False)

# B — within-subgroup vs global sigma. Between-lot shift >> within-lot spread,
# so an SPC limit and a naive global-sigma limit differ by an order of magnitude.
lots, per = 8, 150
offsets = rng.normal(0, 6.0, lots)
pd.DataFrame(
    {
        "lot": np.repeat([f"L{i:02d}" for i in range(lots)], per),
        "cd": np.concatenate([rng.normal(50 + o, 0.4, per) for o in offsets]),
    }
).to_csv("b_within_vs_global.csv", index=False)

# C — heavy tail. mean+7σ is nominally a 1-in-10^12 event; on this lognormal
# 13 of 3000 rows exceed it (1 in 231).
pd.DataFrame({"leak_na": rng.lognormal(1.0, 1.1, 3000)}).to_csv(
    "c_heavy_tail.csv", index=False
)

# D — control. Clean normal: every choice barely moves the answer, so the
# correct behaviour is to answer without asking anything.
pd.DataFrame({"vt_mv": rng.normal(450, 12, 5000)}).to_csv(
    "d_control.csv", index=False
)
```

| # | file | prompt | expected |
|---|---|---|---|
| A | `a_silent_dtype.csv` | "compute mean − 3σ of thickness" | part 2 raises; reports the column arrived as text |
| B | `b_within_vs_global.csv` | "give me the lower limit at mean − 3σ for cd" | part 4 ranks global↔within-lot first; asks with `ask_user` |
| C | `c_heavy_tail.csv` | "compute mean + 7σ of leak_na" | reports the exceedance count against what 7σ implies |
| D | `d_control.csv` | "compute mean − 7σ of vt_mv" | answers with number + chart, asks nothing |

Ground truth, measured from the generated files — what a correct run has to
agree with:

| # | measured |
|---|---|
| A | `thickness` loads as `object`; first value `1,156` |
| B | global σ = 3.853, within-lot σ = 0.392 (**9.8×**); mean − 3σ = **35.36** global vs **45.75** within-lot |
| C | threshold 56.33; **13 of 3000** rows beyond (1 in 231) where normality implies 3.8e-09 |
| D | mean − 7σ = 365.55; the ddof flip moves it 0.0084 (2e-5 relative); 0 rows beyond; min 405.88 |

## Phases

**P1** — draft + fixtures + a live dry run with the body pasted in, no
registration. Kill criterion: `qwen3-local` cannot follow it ⇒ redesign the
body, do not proceed to wiring. A hosted preset runs the same set as the
ceiling reference.

**P2** — wire it (`SHARED_SKILLS` + `app.json`) with a failing test first.

**P3** — live A/B: four scenarios × {Apply chip / skill off}, then a pass on
`read_skill` alone to measure how well the `description` triggers. Failures are
recorded here verbatim, not just summarised.

**P4** — decide from the P3 data: promote to profile default / into
`prompts/system.md`; or harden parts 3–4 into shipped `scripts/`
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

_(Blocked. The local gate could not run: see "P1 run conditions" — ollama here
serves from CPU, `qwen3:14b` exceeds a 600 s timeout on the first call and
`qwen3:8b` costs 193.7 s per step, so a 15-step loop cannot separate "the model
cannot follow this" from "the run did not finish". Pending an endpoint with
working tool-calling.)_

## References

Metamorphic testing: Chen, Cheung & Yiu (1998), *Metamorphic testing: a new
approach for generating next test cases*. Property-based testing: Claessen &
Hughes (2000), *QuickCheck*. N-version programming: Avizienis (1985). One-at-a-
time sensitivity: Saltelli et al., *Global Sensitivity Analysis*.

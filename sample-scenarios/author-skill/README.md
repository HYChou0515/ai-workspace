# author-skill scenarios

Scenarios for tuning the `author-skill` guidance against your own model:

```
python -m workspace_app.skill_eval --dump-skill author-skill -o ./tune
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/author-skill --control -o ./tune/run-1
```

The dump writes `SKILL.md` alone, and an edited copy runs with the registered
skill's `references/` (the run prints which folder supplied them). Half of
this skill's guidance is `references/writing-for-agents.md`, so to tune that
half edit it in place under `sample-skills/author-skill/references/`.

What these measure is whether the model follows the guide's SHAPE, not whether
`save_skill` works (its own tests do that): that it does not save a skill the
user has not seen, and that before drafting it either asks (a run ends at
`ask_user`, as a real turn does) or opens a file. Scoring is on tool names
alone, so each run's `_transcript.json` records which path every `read_file`
asked for: in `reads-the-rules-before-drafting` the writing rules are the only
file staged; `no-save-without-review` also stages `yield.csv`, the worked
example step 2 tells the agent to mine.
`--control` reruns each scenario with no skill loaded, so a scenario the bare
model also passes is reported as measuring nothing.

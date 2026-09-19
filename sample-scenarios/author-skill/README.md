# author-skill scenarios

Scenarios for tuning the `author-skill` guidance against your own model:

```
python -m workspace_app.skill_eval --dump-skill author-skill -o ./tune
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/author-skill --control -o ./tune/run-1
```

What these measure is whether the model follows the guide's SHAPE, not whether
`save_skill` works (its own tests do that): that it opens a file before it
drafts — the writing rules are the only file staged, and each run's
`_transcript.json` records which path was read, since scoring is on tool
names alone — and that it does not save a skill the user has not seen.
`--control` reruns each scenario with no skill loaded, so a scenario the bare
model also passes is reported as measuring nothing.

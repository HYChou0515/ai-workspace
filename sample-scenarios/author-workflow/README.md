# author-workflow scenarios

Scenarios for tuning the `author-workflow` guidance against your own model:

```
python -m workspace_app.skill_eval --dump-skill author-workflow -o ./tune
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/author-workflow --control -o ./tune/run-1
```

The question these answer is not "can the model write a workflow.json" — the
validator inside `save_workflow` already refuses a bad one — but whether the
model reaches for the RIGHT TOOL when the user's words do not name it. "Every
night" names no tool; before this guidance the model answered that no schedule
mechanism exists, because the one file that taught it lived in a skill no app
grants. `--control` reruns each scenario with no skill loaded, so a scenario the
bare model also passes is reported as measuring nothing.

A run ends at `ask_user`, as a real turn does (the answer arrives next turn,
which the harness has no notion of). The guide's step 1 says to get a yes
before drafting, so both scenarios accept an ask-first run as well as a
save-first one — `nightly-report` only when the question itself says when the
job will run — and `_transcript.json` shows which shape ran.

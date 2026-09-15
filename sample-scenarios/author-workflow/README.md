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

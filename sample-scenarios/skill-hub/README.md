# skill-hub scenarios

Scenarios for tuning the `skill-hub` guidance against your own model:

```
python -m workspace_app.skill_eval --dump-skill skill-hub -o ./tune
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/skill-hub --control -o ./tune/run-1
```

What these measure is not whether the three tools work — their own tests do —
but whether the model reaches for the RIGHT one on the user's words, and holds
back where it should: "is there a skill for X" is a search, not a publish;
"save this skill" is `save_skill`, not `publish_skill`; and installing needs a
picked entry, never a guess. `--control` reruns each scenario with no skill
loaded, so a scenario the bare model also passes is reported as measuring
nothing.

# verify-number scenarios

Four questions with known-correct behaviour, for tuning the `verify-number`
guidance against your own model:

```
python -m workspace_app.skill_eval --dump-skill verify-number -o ./tune
python -m workspace_app.skill_eval --skill ./tune/SKILL.md \
    --scenarios sample-scenarios/verify-number --control -o ./tune/run-1
# another of the App picker's presets:  --preset claude-opus
```

Three carry a planted defect the guidance must surface; `control-clean` carries
none, and exists to catch the opposite failure — guidance that cries wolf gets
switched off. Regenerate the CSVs with `python make_data.py` (fixed seed).

Every scenario requires `exec`: a number arrived at in the model's head is not
reproducible and cannot be checked, so "never do arithmetic in-context" is the
one rule with an objective test.

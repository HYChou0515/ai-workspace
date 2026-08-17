---
name: verify-number
description: Use when a number computed from a table or series is going into someone's decision — a limit, a threshold, a rate, a yield, a total. Produces the number together with the evidence that makes it checkable.
---

Nobody can check a number by reading it. `-20.14` and `-20.41` look the same to
the person who has to act on one of them. So hand over the checkable things
next to the number.

Write ONE script that computes and checks itself, run it once, and report from
what it printed.

## `analysis.py`

**1 — the computation, named and alone.**

```python
CHOICES = {"ddof": 0, "scope": "global", "nulls": "drop"}  # what you settled for the user

def compute(df, **choices):
    ...
    return value
```

`CHOICES` holds every fork the request left open and you closed yourself:
sample vs population sigma, pooled vs per-group, whether nulls / zeros /
outliers are in, the time window, which column when the name was ambiguous.

**2 — what you believe about the data, printed and asserted.**

Row count, dtype of every column you use, null count, min, max. Raise when a
numeric column arrived as text, when the row count differs from the file's, or
when nulls disappeared along the way.

**3 — predictions, written before they run.**

Transform the data, and for each transform state the expected result in the code
before comparing: every value ×2, every value +100, rows shuffled, every row
duplicated, the column replaced by one constant. Print expected and actual on
one line each. A mismatch is a defect in `compute` — fix it and run again before
you report anything.

**4 — how much each choice mattered.**

Re-run `compute` with one `CHOICES` entry flipped at a time, and print them
sorted by how far the answer moved. Then write a PNG of the distribution with
the result marked on it.

Print to stdout — that is what comes back to you. Keep the whole report near 100
lines.

## Then

- `show_file` the PNG. A chart you only mention is a chart nobody sees.
- Give the number with n, min, max, how many rows fall beyond it, and the rows
  nearest it.
- Say it plainly when the count beyond the result disagrees with what the
  statistic implies — that is the number telling you its assumptions do not hold
  on this data.
- Ask with `ask_user` about a choice that moved the answer a lot, and only that
  one. Choices that barely moved it belong in the report.
- Leave `analysis.py` in the workspace. The next dataset is then one command,
  and the checks run again with it.

When the number sets a limit someone will be held to, compute it a second time
by an independent route — a different library, or a plain loop — and print both.

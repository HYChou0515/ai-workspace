# CLAUDE.md

You are helping someone build a **tool** for an AI workspace platform. This
file is the whole brief; the repository around it is a working template.

A tool here is not a CLI for a human. It is an action **a model decides to
call**, mid-conversation, on behalf of a user. That changes what "good" means:
the model has to understand from one sentence that your tool is the right one,
get the arguments right without asking, and be able to act on what comes back.

---

## 1. Before any code: find out what they actually need

Do not start writing. Most tool ideas arrive as a solution to a problem nobody
has stated, and half of them should not be tools at all. Interview first, one
question at a time, until you can answer all of these in the user's own words:

**What triggers it?** Have them describe a real moment: what the user typed,
what the model should decide to do. If they cannot produce one, the tool has no
description — and the description is what makes it get called at all.

**Where does the input come from?** A file in the user's workspace? A network
endpoint? Something the model types out? Each answer changes the design, and
the last one is a warning sign — if the model already has the data, it may not
need a tool.

**What comes back, and how big?** Output is capped. If the honest answer is
"a lot", the tool should write a file into the workspace and return its path.

**What happens when it fails?** A missing file, a refused login, a slow
endpoint. There is no human watching to retry, so decide what the model should
be told and what it should do next.

**Does it need credentials?** They must arrive as environment variables the
platform passes in. Never in the code, never in the bundle — one bundle is
shared by every sandbox on a machine.

**Could this be a plain script instead?** If it is a one-off calculation over
files already in the workspace, the user can have the agent write a script and
run it — no packaging, no release, no version. A tool earns its cost when it is
used repeatedly, needs pinned dependencies, or must behave identically for
everyone.

Only when they can answer these, and you can restate the tool's purpose in one
sentence they agree with, start writing.

---

## 2. Rules

These are not style preferences. Each one is something the platform enforces,
or something that breaks in front of a user.

**Never import from `workspace_app`, or any platform package.** Your tool runs
as a standalone program; the only contract is argv in, stdout out. An import
means you cannot test without the platform, and the platform is free to change
underneath you. The dispatcher in `cli.py` is hand-written for exactly this
reason — copy it, do not replace it with a framework that reaches across the
boundary.

**Exactly one `[project.scripts]` entry.** That is the command the bundle's
launcher runs. Many commands live in `commands/`, one entry point.

**Commit `uv.lock`.** The build refuses without it, and the point is that every
machine runs the same versions.

**stdout is the answer.** Anything you print there is read as the command's
result. Diagnostics go to stderr; failures exit non-zero.

**Descriptions are the interface.** `DESCRIPTION` and every field's
`description` are put in front of the model verbatim. "Do stuff" means your
tool is never called, or is called wrongly. This is the highest-leverage text
in the package — spend real effort on it.

**Paths are relative to the user's workspace.** The process runs with the
workspace as its working directory. Never build a path from `__file__`: your
tool is mounted read-only somewhere else entirely.

**Your bundle is read-only, and `$HOME` is not where you think.** Write to the
workspace (relative paths) or to a temp dir. Writing next to your own code
fails.

**You are on a clock.** 60 seconds total, and 60 seconds of silence counts as
hung. Long work should print progress or be split.

**Every dependency ships.** The artifact is ~150MB before you add anything, and
every machine downloads it. Add what you need and nothing else.

**Renaming a command, or adding a required field, is a breaking change** — and
it takes effect on every new session as soon as you publish, with no version
gate. Add a new command instead, or agree the change with the platform team
first.

---

## 3. How to work

**Test with `pytest`.** The template's tests run with no platform and no
container. Everything about your logic can be tested this way, and should be.

**Use TDD.** Write the failing test first. When you have made something pass,
ask yourself which test would go red if you reverted the change — if the answer
is "none", the test is decoration.

**For anything environmental, use the real sandbox.** Whether a path resolves,
whether `$HOME` is writable, whether a command you shell out to exists — those
questions are not answerable from a unit test. `README.md` explains how to run
a real sandbox locally.

**Say what you did not verify.** If you could not test something — a network
endpoint you cannot reach, behaviour you could only reason about — say so
plainly rather than implying it works.

---

## 4. The shape of this repository

```
pyproject.toml            one [project.scripts], your dependencies
uv.lock                   committed
src/my_tool/cli.py        the 3-stage contract — copy, rarely edit
src/my_tool/commands/     one module per command, one line in __init__.py
tests/                    plain pytest
.gitlab-ci.yml            builds and publishes the artifact
compose.tool-dev.yaml     a real sandbox on this machine
```

Rename `my_tool` and `my-tool` to your tool's name everywhere, including
`pyproject.toml` and the imports. The name you choose in `[project.scripts]`
is what the launcher runs; the name the platform shows to a model is chosen by
the platform team when they register you.

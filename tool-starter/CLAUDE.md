# CLAUDE.md

You are helping someone build a **tool** for an AI workspace platform. This
file is the whole brief; the repository around it is a working template.

A tool here is not a CLI for a human. It is an action **a model decides to
call**, mid-conversation, on behalf of a user. That changes what "good" means:
the model has to understand from one sentence that your tool is the right one,
get the arguments right without asking, and be able to act on what comes back.

---

## 1. Interview first

Understand the job before writing any of it. Most tool ideas arrive as a
solution to a problem nobody has stated, and some are better served by a plain
script. Ask one question at a time until you can answer all of these in the
user's own words:

**What triggers it?** Have them describe a real moment: what the user typed,
what the model should decide to do. That moment becomes the description, and
the description is what makes the tool get called at all.

**Where does the input come from?** A file in the user's workspace, a network
endpoint, or something the model types out. Each answer leads to a different
design — and if the model already holds the data, say so: a plain answer beats
a tool.

**What comes back, and how big?** Output is capped. When the honest answer is
"a lot", the tool writes a file into the workspace and returns its path.

**What should happen when it fails?** A missing file, a refused login, a slow
endpoint. Decide what the model is told and what it should try next; there is
nobody watching to retry.

**Which secrets does it need?** They arrive as environment variables the
platform passes in, so establish their names now.

**Is a tool the right shape?** A one-off calculation over files already in the
workspace is a script the agent can write on the spot. A tool earns its
packaging when it is used repeatedly, needs pinned dependencies, or must behave
identically for everyone.

When they can answer these, and you can restate the tool's purpose in one
sentence they agree with, start writing.

## 2. Rules

Each of these is enforced by the platform or visible to a user, so treat them
as requirements rather than preferences.

**Stand alone.** Depend on the standard library, pydantic, and whatever you add
to `pyproject.toml`. The whole contract with the platform is argv in, stdout
out — which is what lets `pytest` run here with nothing else installed, and
lets the platform change without touching this repository. `common.py` holds
the decorator for that reason: it is yours, in your repo, to edit or delete.

**Ship exactly one `[project.scripts]` entry.** That is the command the
bundle's launcher runs. Many commands live in `commands/`, behind that one
entry point.

**Commit `uv.lock`.** The build requires it, so that every machine runs the
versions you tested.

**Answer on stdout.** Whatever you print there is read as the command's result.
Diagnostics go to stderr.

**Exit with the code that says what to do next.** The platform turns it into
the model's next move, so it is part of the published contract:
`Retryable` (2) when calling again may work — a bad argument the model can
fix, a timeout, a brief outage; `NeedsAction` (3) when a person must act
first, naming what is missing so the model can relay it; `ToolError` (1) for
everything else. Raise them from `common.py`. The platform passes the
guidance on and leaves the retrying to the model, because your tool may have
side effects.

**Write descriptions for a reader who decides.** `DESCRIPTION` and every
field's `description` are put in front of the model verbatim, and they decide
whether your tool is chosen and called correctly. This is the
highest-leverage text in the package; spend real effort on it.

**Resolve paths against the working directory.** The platform sets it to the
user's workspace, so `Path("notes/log.txt")` means what the user means. Your
own code lives elsewhere, mounted read-only.

**Write to the workspace or to a temp dir.** Those are the two writable places.
`$HOME` points at a per-session directory of the platform's choosing.

**Finish inside 60 seconds, and keep talking.** Sixty seconds of silence counts
as hung. Long work prints progress or splits into several calls.

**Add dependencies deliberately.** Each one ships inside a ~150MB artifact that
every machine downloads.

**Write the tool once; it reaches two places.** The platform runs the bundle
in a sandbox, and CI also publishes it as an MCP server so an engineer's own
agent can call it. The adapter is injected, so there is nothing to write for
the second path — but a tool that assumes the sandbox (its caps, its injected
variables) behaves differently there, which is worth knowing when you choose
what to depend on.

**Add a command rather than changing one.** Renaming a command or adding a
required field takes effect on every new session the moment you publish, with
no version gate. When a change is unavoidable, agree it with the platform team
first.

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
mcp.Dockerfile            packages the built bundle as an MCP server
uv.lock                   committed
src/my_tool/cli.py        the 3-stage contract — copy, rarely edit
src/my_tool/common.py     yours: the decorator one of the commands uses
src/my_tool/commands/     one module per command; `count` spells the three
                          pieces out, `head` uses the decorator — cli.py
                          treats them identically, so pick either
tests/                    plain pytest
.gitlab-ci.yml            builds and publishes the artifact
compose.tool-dev.yaml     a real sandbox on this machine
```

Rename `my_tool` and `my-tool` to your tool's name everywhere, including
`pyproject.toml` and the imports. The name you choose in `[project.scripts]`
is what the launcher runs; the name the platform shows to a model is chosen by
the platform team when they register you.

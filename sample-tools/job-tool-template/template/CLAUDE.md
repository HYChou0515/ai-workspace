# CLAUDE.md

You are helping someone connect a **job backend** — a scheduler, a batch
system, an approval queue — to an AI workspace as a tool. The repository is a
working template: the commands, the time budgets and everything the model is
told are already written. What is missing is the backend package, which
implements `JobBackend` from `job-contract`. Read `README.md` first.

## 1. Interview first

Ask one question at a time until you can answer these in the user's words:

**What is a job here?** One real example: what gets submitted, where it runs,
how long it takes. That becomes `domain_description` and `spec_schema`.

**When are two submits the same job?** Which spec fields decide it — within
one item, for `idempotency.window_seconds` (a day unless they say otherwise).
This is `idempotency.fields`, and getting it wrong is either two jobs for one
request or one job for two. If "the same" needs loosening (case, spaces, a
nested field), override `identity(spec)` and give `example_spec`.

**Whose credential is it, and who should share it?** The token decides what
the backend shows and lets anyone change. Put it in the item's environment
variables and everyone who can open the item shares those jobs; in a person's
Private layer and they are that person's alone.

**Which optional calls does the backend support?** Change a job, cancel it,
read its log, fetch its outputs. Write only those; the rest are never offered.

**How long does each call really take?** Against the defaults in `README.md`.
A call that cannot fit its budget needs a faster path (a cache, a cheaper
endpoint), not a bigger number — the platform stops every call at 60 s.

**Which environment variables?** Names, and what each is for. They go in
`Contract.env`.

## 2. Rules

**Write the backend package, not this repository.** Everything here is
generated from the backend's `Contract`. Edit the template only on purpose,
and know that its tests are what CI runs before every build.

**`submit` with a key seen within the window returns that job, as
`Submitted(job_id, created=False)`.** No test can check it here; it is the
promise only the backend can keep. Record the `item_id` it is given and report
it back on every `JobStatus`.

**`phase` is `running`, `done` or `unknown`.** The backend's own words go in
`state`. `unknown` is "could not ask", never "does not exist".

**Name the variable in a `NeedsAction`.** `set GPU_API_TOKEN`, not "log in":
the name is what lets the platform offer the person a way to set it.

**After changing `Contract.env`, run `uv run python -m job_tool.envfile`.** The shape
tests fail until `env.json` matches.

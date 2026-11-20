# CLAUDE.md

You are helping someone connect **their machines** — a GPU cluster, a batch
system, a host with a licence — to an AI workspace as a tool. The repository is
a working template: packing a folder, uploading it in pieces, starting it,
following it, bringing results back, and everything the model is told are
already written. What is missing is the backend package, which implements
`JobBackend` from `job-contract`. Read `README.md` first.

## 1. Interview first

Ask one question at a time until you can answer these in the user's words:

**How does a job run on your machines?** One real example: what is submitted
(`sbatch`, a k8s Job, a REST call), where its files must be, how long it
takes. That decides `upload`, `start` and `status`.

**What must a folder contain?** Which file is run, and how (`bash run.sh`)?
Which files set up the environment (`requirements.txt`, `environment.yml`)
and what happens without them? Where do a job's variables go (`.env`)? What
does the machine have — Python and CUDA versions, datasets already there,
network or not — and which secrets are already set for a job? All of it
becomes `run_description`, the only thing the model reads about your machines.

**What may a job ask for?** GPUs, memory, hours, a partition, a priority, and
the limits of each. That becomes `resources_schema`.

**Whose credential does the tool use, and who should share it?** The token
decides what the backend shows and lets anyone change. In the item's
environment variables, everyone who can open the item shares those jobs; in a
person's Private layer, they are that person's alone. It goes in `env`.

**Can the machines report the queue?** Workers, waiting jobs, quota. If so,
write `capacity` and the model gets `queue`.

**How long does each call really take?** Against the defaults in `README.md`.
A call that cannot fit its budget needs a faster path, not a bigger number —
the platform stops every call at 60 s.

## 2. Rules

**Write the backend package, not this repository.** Edit the template only on
purpose, and know that its tests are what CI runs before every build.

**`upload` writes at the offset and never truncates; `list_files` reports
real sizes, before `start` too.** The tool resumes from those sizes. No test
here can check your service keeps this; it is yours to keep.

**`start` of an id already started does nothing.** The tool may call it again
after a lost answer.

**`phase` is `queued`, `running`, `succeeded`, `failed` or `unknown`.** The
backend's own words go in `state`. `unknown` is "could not ask", never "does
not exist" — that is `NotFound`.

**Name the variable in a `NeedsAction`.** `set CLUSTER_TOKEN`, not "log in":
the name is what lets the platform offer the person a way to set it.

**After changing `env`, run `uv run python -m job_tool.envfile`.** The shape
tests fail until `env.json` matches.

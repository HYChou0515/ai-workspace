# job-tool-template

A template for tools that send a folder of the workspace to another machine —
a GPU cluster, a batch farm, a licensed host — run it there, and bring the
results back. The tool packs the folder, uploads it in pieces, starts it, and
answers "is it done yet?" in one call; whoever runs the machines writes only
the part that moves bytes to them.

- `template/` — the tool. Start with its `README.md`.
- `contract/` — `job-contract`, the interface both sides depend on.
- `example-impl/` — a demo backend, the start of yours.

It imports nothing from ai-workspace; to the platform it is one more
third-party tool. Design and decisions: `docs/plan-job-queue.md`.

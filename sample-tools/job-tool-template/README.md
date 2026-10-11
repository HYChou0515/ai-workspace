# job-tool-template

A template for tools that submit work somewhere else and ask about it later.
We define the interface — shape, per-call time budget, what each outcome means
— and whoever runs the backend writes only the part that talks to it.

- `template/` — the tool. Start with its `README.md`.
- `contract/` — `job-contract`, the interface both sides depend on.
- `example-impl/` — a demo backend, the start of yours.

It imports nothing from ai-workspace; to the platform it is one more
third-party tool. Design and decisions: `docs/plan-job-tool-template.md`.

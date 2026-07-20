# Wiki conventions (WIKI.md)

This folder is a **knowledge wiki** for one collection — a set of interlinked
markdown pages you (an AI) own and maintain. People read it; only you write it.
You build it once during ingest and keep it current, so answers don't have to
re-derive everything from raw sources every time.

## Layout

- `index.md` — the home page. A short orientation + links to the main entity
  and concept pages. Keep it current as pages are added.
- `entities/<name>.md` — one page per concrete thing (a tool, a lot, a part, a
  process step, a person, a machine). Facts about that entity.
- `concepts/<name>.md` — one page per idea/topic that spans entities (a failure
  mode, a method, a metric, a standard).
- `log.md` — an append-only ingest log. One line per source you process, with a
  **consistent prefix** so it stays greppable, e.g.:
  `## [ingest] <source path> — <one-line summary>`

Use lowercase, hyphenated file names (`reflow-zone-3.md`).

## Cross-linking

Link related pages with `[[wikilinks]]` using the page's path-stem, e.g.
`[[reflow-zone-3]]` or `[[voiding]]`. Link liberally — the interconnections are
the point. When you mention an entity/concept that has (or should have) a page,
link it.

## Provenance (required)

Every page ends with a `Sources:` line listing the **source document paths** the
page's facts came from, e.g.:

```
Sources: reflow-spec.pdf · qual-report-25w14.md
```

This is how a reader traces a claim back to the real document, so keep it
accurate when you add or change facts.

## Contradictions

If a new source conflicts with what a page says, **don't silently overwrite** —
note the conflict inline (e.g. `> ⚠ Conflict: spec says 245 °C, qual report
observed 250 °C (qual-report-25w14.md)`), so the disagreement is visible.

## Style

Concise, factual, skimmable. Headings, short bullets. No filler. This is a
reference, not an essay.

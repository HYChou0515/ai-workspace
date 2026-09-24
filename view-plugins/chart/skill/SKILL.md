---
name: chart
description: Use when a claim rests on a relationship, trend, distribution or comparison in a table, or on a value's pattern across a 2-D lattice. Writes a live chart the user opens from the reply, with the rows behind the claim lit.
---

A claim about data convinces when the person can see it. Put the claim in a
chart file, show it, and point at what is lit.

## Steps

1. **Write the chart file** with `write_file`: `views/<what-it-shows>.ai.yaml`,
   using the spec below. Put the rows your claim is about in `highlight:`.
2. **Show it** with `show_file` on that path. The reply carries one summary
   line, such as `highlight matches 3/25 rows; fail_rate 0.02–0.41`. When the
   reply names a problem instead, fix that key and show the file again.
3. **Answer** with the claim, what the lit marks are, and the summary's
   numbers.

Done when `show_file` has returned a summary line and your answer names what is
lit.

## The spec

```yaml
view: chart                      # always
title: Fail rate rises with thickness
source: data/wafers.csv          # a .csv / .tsv / .parquet in the workspace,
                                 # or {entity: <type>} for entity records
keys: [lot, wafer]               # the columns that name one row
mark: scatter
encoding:
  x: {field: thickness, type: quantitative}
  y: {field: fail_rate, type: quantitative}
  color: {field: lot, type: nominal}
  tooltip: [{field: wafer, type: nominal}]
highlight:
  where: "fail_rate > 0.3"       # a pandas query over the chart's rows
```

Every key sits in this list; the file is checked against it.

- `view`, `source` and either `mark` + `encoding` or `layer` are required.
- `title`, `description`: text.
- `keys`: column names.
- `highlight`: `{where: "<pandas query>"}`, or `{values: {<column>: [v, …]}}`
  for rows whose column holds one of the values. It runs over each layer's
  rows after its transforms, so it can name an aggregate's `as`.
- `transform`: a list, applied in order (below).
- `bin_threshold`: scatters with more points than this (default 10000) are
  drawn as counted bins.
- `layer`: a list of `{mark, encoding, transform?}` drawn on the same axes,
  after the top-level `transform`.

### Marks

`mark: <name>`, or `mark: {type: <name>, …}` with `color`, `opacity` (0–1),
`point` and `smooth` (line), `stack` (bar, area), `extent` (errorbar:
`stderr` / `stdev` / `iqr`).

| mark | shows |
|---|---|
| `scatter` | how two measures move together |
| `line`, `area` | a measure over an ordered axis, usually time |
| `bar` | a measure per category |
| `pie` | shares of a whole: `theta` + `color` |
| `heatmap` | a measure over two categories |
| `grid` | a measure's spatial pattern over a 2-D integer lattice (`x`, `y`, `color`) |
| `boxplot` | the spread of a measure per group |
| `errorbar` | a group's mean with its error; or `y` to `y2` as given |
| `rule` | a reference line: `y: {datum: 105.5}` or `x: {datum: …}` |
| `text` | labels at points: `text: {field: …}` |

### Encoding

Channels: `x`, `y`, `x2`, `y2`, `color`, `size`, `theta`, `text`, and
`tooltip` (one channel or a list). Each is `{field: <column>, type: <type>}`
with optional `aggregate`, `title`, `sort` (`ascending`, `descending` or a list
of values), `scale` (`type: log`, `zero: true` / `false`, `domain: [lo, hi]`,
`scheme: sequential` or `diverging`), or `{datum: <value>, title}` for a
constant. A number axis includes zero for `bar` and `area`, where length is
the value, and fits the data for every other mark; `zero:` overrides either.

Types: `quantitative` (numbers), `temporal` (dates and times), `nominal`
(categories), `ordinal` (ordered categories).

`aggregate` on a channel (`count`, `sum`, `mean`, `min`, `max`, `rate`) groups
by every other field channel, as in Vega-Lite. `rate` is the share of rows
where the field is true.

### Transforms

```yaml
transform:
  - filter: "tool != 'T9'"                        # a pandas query
  - filter: {field: layer, oneOf: [M1, M2]}       # or equal / range / lt / lte / gt / gte / valid
  - aggregate: [{op: rate, field: fail, as: fail_rate}, {op: count, as: n}]
    groupby: [die_x, die_y]
  - diff: {by: phase, of: after, minus: before}   # the aggregate where phase is after,
    aggregate: [{op: mean, field: t, as: delta}]  # minus where it is before,
    groupby: [die_x, die_y]                       # per group both sides have
```

## Shapes that carry a claim

- **A value's pattern across a 2-D lattice**: `grid`, with an `aggregate`
  grouped by the lattice columns.
- **The change between two conditions**: a `diff` transform, drawn as `grid` or
  `heatmap` with `scale: {scheme: diverging}`.
- **A limit or target**: a `layer` with the data mark and a `rule` at the limit.
- **Variation per group**: `boxplot`, or `errorbar` layered over `bar`.
- **A trend**: `line` on a `temporal` x, `color` per series.
- **Which members stand out**: `highlight` them, with `keys:` naming the columns
  that identify a member.

---
name: chart
description: Use when a claim rests on a relationship, trend, distribution or comparison in a table, or on a value's pattern across a 2-D lattice. Writes a live chart the user opens from the reply, with the rows behind the claim lit.
---

A claim about data convinces when the person can see it. Put the claim in a
chart file, show it, and point at what is lit.

## Steps

1. **Write the chart file** with `write_file`: `views/<what-it-shows>.ai.yaml`,
   using the spec below. Put the rows your claim is about in `highlight:`.
2. **Show it** with `show_file` on that path. The file is checked first:
   - a checked file is shown, and the reply ends with one summary line, such
     as `highlight matches 3/25 rows; error_rate 0.02–0.41`;
   - a reply starting `error:` says what to fix (a key, a column, the YAML)
     — fix it and show the file again;
   - a reply noting `could not check this view` has shown the file as it is.
3. **Answer** with the claim, what the lit marks are, and the summary's
   numbers when there is a summary.

Done when `show_file` has shown the file and your answer names what is lit.

## The spec

```yaml
view: chart                      # always
title: Error rate rises with size
source: data/measurements.csv    # a .csv / .tsv / .parquet in the workspace,
                                 # or {entity: <type>} for entity records
keys: [group, id]                # the columns that name one row
mark: scatter
encoding:
  x: {field: size, type: quantitative}
  y: {field: error_rate, type: quantitative}
  color: {field: group, type: nominal}
  tooltip: [{field: id, type: nominal}]
highlight:
  where: "error_rate > 0.3"      # a pandas query over the chart's rows
```

The keys to use:

- `view`, `source` and either `mark` + `encoding` or `layer` are required.
- `title`, `description`: text.
- `keys`: column names.
- `marking`: a name. Charts with the same `marking` are linked: rows the person
  brushes in one light up in the others, matched on the columns named in the
  brushing chart's `keys`.
- `highlight`: `{where: "<pandas query>"}`, or `{values: {<column>: [v, …]}}`
  for rows whose column holds one of the values. It runs over each layer's
  rows after its transforms, so it can name an aggregate's `as`.
- `transform`: a list, applied in order (below).
- `bin_threshold`: a scatter of two number / time axes with more points than
  this (default 10000) is drawn as counted bins.
- `layer`: a list of `{mark, encoding, transform?}` drawn on the same axes,
  after the top-level `transform`.

### Marks

`mark: <name>`, or `mark: {type: <name>, …}` with `color`, `opacity` (0–1;
scatter, line, bar, text; an area's fill), `point` and `smooth` (line, area), `stack`
(bar, area), `extent` (errorbar: `stderr` / `stdev` / `iqr`).

| mark | shows | needs |
|---|---|---|
| `scatter` | how two measures move together | `x`, `y` fields |
| `line`, `area` | a measure over an ordered axis, usually time | `x`, `y` fields |
| `bar` | a measure per category | `x`, `y` fields |
| `pie` | shares of a whole | a `theta` field; `color` names the slices |
| `heatmap` | a measure over two categories | `x`, `y`, `color` fields |
| `grid` | a measure's spatial pattern over a 2-D integer lattice | `x`, `y`, `color` fields |
| `boxplot` | the spread of a measure per group | a `y` field |
| `errorbar` | per group, the mean ± `stderr` or `stdev`, or the median with its quartiles (`iqr`); or `y` to `y2` as given | a `y` field |
| `rule` | a reference line: `y: {datum: 105.5}` or `x: {datum: …}` | `x` or `y` |
| `text` | labels at points | `x`, `y`, a `text` field |

### Encoding

Channels: `x`, `y`, `x2`, `y2`, `color`, `size`, `theta`, `text`, and
`tooltip` (one channel or a list). Each is `{field: <column>, type: <type>}`
with optional `aggregate`, `title`, `sort` (`ascending`, `descending` or a list
of values) and `scale` — or, on a `rule`'s `x` or `y` only, `{datum: <value>, title}`
for a constant (a rule given both draws its `y`).

`scale` on `x` / `y`: `type: log`, `zero: true` / `false`, `domain: [lo, hi]`.
`scale` on `color`: `scheme: sequential` or `diverging`. When any layer is a
`bar` or `area` (length is the value) the number axes include zero; otherwise
they fit the data; `zero:` overrides either.

Types: `quantitative` (numbers), `temporal` (dates and times; a number is
epoch milliseconds), `nominal` (categories), `ordinal` (ordered categories).
On a temporal axis a `datum` is a number (epoch milliseconds) or a date
written `2024-03-01`, `2024-03-01T12:00` (read as UTC) or
`2024-03-01T12:00:00+08:00` (`/` for `-` and a space for `T` work too); on a
number axis it is a finite number (above 0 on a log scale), never text; on a
category axis, a value the axis shows; on a grid, a cell, or a number or date
between two cells. validate refuses any other datum, and a datum on a chart
where no layer draws a field on that axis.

`aggregate` on a channel (`count`, `sum`, `mean`, `min`, `max`, `rate`) groups
by every other field channel, as in Vega-Lite. Unlike Vega-Lite, a channel's
`count` names a `field` and counts the rows where that field has a value. `rate` is the share of rows
where the field is true; its field holds true/false or 0/1.

A cell holding a list (an entity list, a parquet list column) is one text,
written as Python prints it — `['a', 'b']`: `equal`, `oneOf`,
`diff` and `highlight: values` compare that text; to test membership use a
query filter, `filter: "tags.str.contains('a', regex=False, na=False)"` (an item equal to
`a`), or `"tags.str.len() > 1"`.

### Transforms

```yaml
transform:
  - filter: "status != 'draft'"                   # a pandas query
  - filter: {field: region, oneOf: [north, south]} # or equal / range / lt / lte / gt / gte / valid
  - aggregate: [{op: rate, field: failed, as: fail_share}, {op: count, as: n}]
    groupby: [cell_x, cell_y]
  - diff: {by: phase, of: after, minus: before}   # the aggregate where phase is after,
    aggregate: [{op: mean, field: t, as: delta}]  # minus where it is before,
    groupby: [cell_x, cell_y]                     # per group both sides have
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
- **One set of rows seen several ways**: give each chart the same `marking:`
  and `keys:`, then show them together with `show_file(layout=…)`, one chart
  per pane. What the person brushes in one lights the rest.

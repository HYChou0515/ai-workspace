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
- `keys`: column names (a column no row holds a value of is refused).
- `marking`: a name. Charts with the same `marking` are linked: rows the person
  selects in one (a box or a lasso on any mark but a pie or a rule, a click on a
  pie's slice, a legend click) light up in the others, matched on the columns named
  in the selecting chart's `keys`. A field a channel aggregates holds the aggregate,
  not the field's values, so it is never matched: a pie of
  `theta: {field: id, type: quantitative, aggregate: count}` links by its `color` field.
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
(bar, area; raw rows add up per slot, no `aggregate: sum` needed: each colour's rows at a slot are summed into one
segment. A stack links by its slot and colour only; `highlight:` may also test its value, which is each segment's
sum (or its own aggregate). In a chart that is only stacks, any other field in `keys:` (one no stack uses as its
slot or colour) or `highlight:` (nor as its value) is refused, since a segment has no single value of it; beside an
unstacked layer whose rows carry the field (e.g. points) it is accepted and only that layer writes and lights by it. A tooltip may not aggregate the stack's value field by another op. The value channel is quantitative, and a
stack is coloured by a category — a quantitative colour is refused), `extent` (errorbar: `stderr` / `stdev` / `iqr`).

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
written `2024-03-01`, `2024-03-01T12:00` (read on the axis's clock: the zone
the field's data carries, UTC when it carries none; a time that zone had twice
or never is refused, so write it with its offset) or
`2024-03-01T12:00:00+08:00` (`/` for `-` and a space for `T` work too); on a
number axis it is a finite number (above 0 on a log scale), never text; on a
category axis, a value the axis shows; on a grid, a cell, or a number or date
between two cells. validate refuses any other datum, and a datum on a chart
where no layer draws a field on that axis. A filter, `diff` or
`highlight: values` value on a date
field (a CSV column of such dates too) takes the same date forms, a marked
value's text, or a number; a time without a zone is read in the field's zone,
and as UTC when the field has none. In a pandas query (`filter: "…"`,
`where:`) a time compared with a zoned field carries its zone
(`'2024-03-01T12:00+08:00'`).

`aggregate` on a channel (`count`, `sum`, `mean`, `min`, `max`, `rate`) groups
by every other field channel, as in Vega-Lite. Unlike Vega-Lite, a channel's
`count` names a `field` and counts the rows where that field has a value. `rate` is the share of rows
where the field is true; its field holds true/false or 0/1.

A cell holding a list (an entity list, a parquet list column) is one text,
written as Python prints it — `['a', 'b']`: `equal`, `oneOf`,
`diff` and `highlight: values` compare that text. Where a field's cells are
lists, a query filter tests membership:
`filter: "tags.str.contains('a', regex=False, na=False)"` (an item equal to
`a`), or `"tags.str.len() > 1"` (more than one item).

### Transforms

```yaml
transform:
  - filter: "status != 'draft'"                   # a pandas query
  - filter: {field: region, oneOf: [north, south]} # or equal / range / lt / lte / gt / gte / valid
  - aggregate: [{op: rate, field: failed, as: fail_share}, {op: count, as: n}]
    groupby: [cell_x, cell_y]
  - diff: {by: phase, of: after, minus: before}   # the aggregate where phase is after,
    aggregate: [{op: mean, field: t, as: delta}]  # minus where it is before,
    groupby: [cell_x, cell_y]                     # per group either side has (count, sum: missing side 0; other ops: empty); signed
```

### Facet: one small grid per group

```yaml
facet: {field: [batch, unit], sort: {field: fail_rate, order: descending}}
```

`facet` draws the `grid` once per group, as a gallery of small maps. It works for
hundreds or thousands of groups, because only what is on screen is loaded.

- `field` names the column or columns that identify a group.
- `sort` orders the gallery by a column: its one value per group, or, with
  `stat` (`count`, `distinct`, `min`, `max`, `mean`, `median`), a statistic of
  a column with several values per group. The person can re-sort by any
  column in the gallery.
- The source is a table file (CSV, TSV, parquet); an entity source is refused.
- It needs `mark: grid`, with `x`, `y` and a `color` field and no `aggregate` on
  them. To reduce rows first, aggregate in `transform:` with the facet columns in
  its `groupby`.
- The person can select a run of groups (ranks 1–30, say). The selection goes to
  the gallery's `marking:` under the facet columns, so other views on that marking
  light the same groups. The person can also stack the selected groups into one
  map, and subtract a second set; to show a stack yourself, use `grid` with an
  `aggregate` over the groups (and `diff` for A − B).

## Shapes that carry a claim

- **A value's pattern across a 2-D lattice**: `grid`, with an `aggregate`
  grouped by the lattice columns.
- **The change between two conditions**: a `diff` transform, drawn as `grid` or
  `heatmap` with `scale: {scheme: diverging}`.
- **Many groups that share a shape**: `facet` over a `grid`, sorted by the
  column that ranks them, on a `marking:`, so the ones that stand out can be
  selected and seen in the other views.
- **A limit or target**: a `layer` with the data mark and a `rule` at the limit.
- **Variation per group**: `boxplot`, or `errorbar` layered over `bar`.
- **A trend**: `line` on a `temporal` x, `color` per series.
- **Which members stand out**: `highlight` them, with `keys:` naming the columns
  that identify a member.
- **One set of rows seen several ways**: give each chart the same `marking:`
  and `keys:`, then show them together with `show_file(layout=…)`, one chart
  per pane. What the person selects in one lights the rest. A marking over
  several `keys:` columns lights every combination of their values, so its count
  can exceed the rows picked; the views say "by <columns>" beside it. A `csv-table` view
  (`view: csv-table`, `source:`) on the same `marking:` shows the lit rows, and
  selecting rows there lights the charts. The person can save a marking's rows
  as `markings/<name>-<yyyymmdd-hhmm>.csv`; read it like any table file.

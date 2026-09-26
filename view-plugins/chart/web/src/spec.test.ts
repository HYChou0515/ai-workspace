/**
 * The spec corpus, read the way the host reads a view file.
 *
 * `view-plugins/chart/spec-corpus/` is shared with the sandbox's own test
 * (`sandbox-src/tests/test_spec_corpus.py`), which runs the same files through
 * its YAML reader and the same schema file. The verdict comes from the file name
 * (`ok-*` / `bad-*`), so both readers are held to the same answer.
 *
 * `ok-*.expect.json` is the document js-yaml — the host's parser — produces.
 * This test pins it; the sandbox's test then holds its own reader to it.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { load } from "js-yaml";
import { describe, expect, it } from "vitest";

import { specErrors } from "./spec";

const corpus = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "spec-corpus");
const files = readdirSync(corpus)
  .filter((n) => n.endsWith(".ai.yaml"))
  .sort();

function verdict(text: string): string[] {
  let doc: unknown;
  try {
    doc = load(text);
  } catch (e) {
    return [`not valid YAML: ${(e as Error).message}`];
  }
  return specErrors(doc);
}

describe("spec corpus", () => {
  it("has both verdicts and nothing else", () => {
    expect(files.some((n) => n.startsWith("ok-"))).toBe(true);
    expect(files.some((n) => n.startsWith("bad-"))).toBe(true);
    expect(files.every((n) => n.startsWith("ok-") || n.startsWith("bad-"))).toBe(true);
  });

  it.each(files)("%s gets the verdict its name says", (name) => {
    const errors = verdict(readFileSync(join(corpus, name), "utf8"));
    if (name.startsWith("ok-")) expect(errors).toEqual([]);
    else expect(errors.length).toBeGreaterThan(0);
  });

  it.each(files.filter((n) => n.startsWith("ok-")))("%s's expect.json is what js-yaml reads", (name) => {
    const expected = JSON.parse(readFileSync(join(corpus, name.replace(/\.ai\.yaml$/, ".expect.json")), "utf8"));
    expect(load(readFileSync(join(corpus, name), "utf8"))).toEqual(expected);
  });
});

describe("messages", () => {
  const base = "view: chart\nsource: data/a.csv\n";
  const enc = "encoding:\n  x: {field: a, type: quantitative}\n  y: {field: b, type: quantitative}\n";

  it("states the schema's own sentence for a choice between mark and layer", () => {
    expect(verdict(base + "title: t\n")).toEqual([
      "(top level): draw with either mark + encoding, or layer — not both, and not neither",
    ]);
  });

  it("states it for a source that is not a table file", () => {
    const [line] = verdict("view: chart\nsource: data/a.xlsx\nmark: line\n" + enc);
    expect(line).toContain("source: ");
    expect(line).toContain(".csv, .tsv or .parquet");
  });

  it("names the key for an unsupported mark, and the marks there are", () => {
    const lines = verdict(base + "mark: geoshape\n" + enc);
    expect(lines.every((l) => l.startsWith("mark"))).toBe(true);
    expect(lines.join("\n")).toContain("scatter, heatmap");
  });

  it("names an unknown key", () => {
    expect(verdict(base + "colour: red\nmark: line\n" + enc)).toContain("(top level): unknown key 'colour'");
  });

  // Review round 8: a datum off a rule's x / y said only "must have required
  // property 'field'", and a model tried another datum.
  const offRule = "encoding.y2: a datum is drawn only as a rule's x or y — drop it and name a field here";
  it("says why a datum is refused where it is not drawn", () => {
    const area = "mark: area\nencoding:\n  x: {field: a, type: quantitative}\n  y: {field: b, type: quantitative}\n  y2: {datum: 0}\n";
    expect(verdict(base + area)).toEqual([offRule]);
  });

  it("gives one line, with the reason, for a line's x datum", () => {
    const line = "mark: line\nencoding:\n  x: {datum: 1}\n  y: {field: b, type: quantitative}\n";
    expect(verdict(base + line)).toEqual(["encoding.x: a datum is drawn only as a rule's x or y — drop it and name a field here"]);
  });

  it("says it once for a mark that also needs the field", () => {
    const grid = "mark: grid\nencoding:\n  x: {datum: 1}\n  y: {field: b, type: ordinal}\n  color: {field: c, type: quantitative}\n";
    const lines = verdict(base + grid).filter((l) => l.startsWith("encoding.x:"));
    expect(lines).toEqual(["encoding.x: a datum is drawn only as a rule's x or y — drop it and name a field here"]);
  });

  // #847/#848 PR 5 P40 row 18: a stack is summed per slot and colour, and a
  // colour by value makes no segments. test_spec_messages.py reads the same.
  const byValue =
    "a stack cannot be coloured by value (quantitative): which rows form one segment of it " +
    "is not defined — colour it by a category (nominal or ordinal), or drop stack";
  const stackEnc = [
    "encoding:",
    "  x: {field: a, type: nominal}",
    "  y: {field: b, type: quantitative}",
    "  color: {field: c, type: quantitative}",
  ];
  it("says why a stack coloured by value is refused", () => {
    expect(verdict(`${base}mark: {type: bar, stack: true}\n${stackEnc.join("\n")}\n`)).toEqual([`encoding.color: ${byValue}`]);
  });

  it("says it of a layer's stack too", () => {
    const layered = `layer:\n  - mark: {type: area, stack: true}\n${stackEnc.map((l) => `    ${l}\n`).join("")}`;
    expect(verdict(base + layered)).toEqual([`layer[0].encoding.color: ${byValue}`]);
  });

  it.each([
    ["{type: bar, stack: true}", "nominal"],
    ["{type: area, stack: true}", "ordinal"],
    ["{type: area, stack: true}", "temporal"],
    ["{type: bar, stack: false}", "quantitative"],
    ["{type: bar}", "quantitative"],
    ["{type: line, stack: true}", "quantitative"],
    ["bar", "quantitative"],
  ])("does not refuse %s coloured by a field of type %s", (mark, colour) => {
    const enc = "encoding:\n  x: {field: a, type: nominal}\n  y: {field: b, type: quantitative}\n";
    expect(verdict(`${base}mark: ${mark}\n${enc}  color: {field: c, type: ${colour}}\n`)).toEqual([]);
  });

  // #847/#848 PR 5 P41 row 25: a stack sums its value channel; a time summed
  // nanoseconds and a category summed text. test_spec_messages.py reads the same.
  const stackValue =
    "a stack sums its rows, so its value channel must be quantitative — a time or " +
    "a category has no sum: make it quantitative, or drop stack";
  const stack = (mark: string, x: string, y: string) =>
    `${base}mark: ${mark}\nencoding:\n  x: {field: a, type: ${x}}\n  y: {field: b, type: ${y}}\n  color: {field: c, type: nominal}\n`;

  it.each([
    ["{type: bar, stack: true}", "nominal", "temporal", "y"],
    ["{type: area, stack: true}", "ordinal", "nominal", "x"],
    ["{type: area, stack: true}", "temporal", "ordinal", "x"],
    ["{type: bar, stack: true}", "temporal", "ordinal", "x"],
    ["{type: bar, stack: true}", "nominal", "nominal", "x"],
  ])("says why %s over x %s, y %s is refused, on its value channel", (mark, x, y, channel) => {
    expect(verdict(stack(mark, x, y))).toEqual([`encoding.${channel}: ${stackValue}`]);
  });

  it("says it of a layer's stack too", () => {
    const layered = `${base}layer:\n  - mark: {type: bar, stack: true}\n    encoding:\n      x: {field: a, type: nominal}\n      y: {field: b, type: temporal}\n`;
    expect(verdict(layered)).toEqual([`layer[0].encoding.y: ${stackValue}`]);
  });

  it.each([
    ["{type: bar, stack: true}", "nominal", "quantitative"],
    ["{type: bar, stack: true}", "temporal", "quantitative"],
    ["{type: bar, stack: true}", "quantitative", "nominal"],
    ["{type: area, stack: true}", "quantitative", "ordinal"],
    ["{type: bar, stack: false}", "nominal", "temporal"],
    ["{type: line, stack: true}", "nominal", "temporal"],
    ["bar", "quantitative", "ordinal"],
  ])("does not refuse %s over x %s, y %s", (mark, x, y) => {
    expect(verdict(stack(mark, x, y))).toEqual([]);
  });

  // #847/#848 PR 5 P41 row 26: a layer aggregates a field once (the first op
  // won), and a tooltip labelled max showed the mean. test_spec_messages.py
  // reads the same.
  const twoOps = (y: string, tip: string) =>
    `${base}mark: bar\nencoding:\n  x: {field: item, type: nominal}\n  y: {field: value, type: quantitative${y}}\n` +
    `  tooltip:\n    - {field: item, type: nominal}\n    - {field: value, type: quantitative${tip}}\n`;

  it("refuses one field aggregated two ways, saying why", () => {
    expect(verdict(twoOps(", aggregate: mean", ", aggregate: max"))).toEqual([
      "encoding.tooltip[1]: 'value' is aggregated as mean on y — a field has one " +
        "aggregate in a layer, so max here would show the mean: use mean here too, " +
        "or compute both in a transform aggregate, each under its own name (as:)",
    ]);
  });

  it("refuses it on a single tooltip", () => {
    const text =
      `${base}mark: bar\nencoding:\n  x: {field: item, type: nominal}\n` +
      "  y: {field: value, type: quantitative, aggregate: mean}\n" +
      "  tooltip: {field: value, type: quantitative, aggregate: max}\n";
    const [line] = verdict(text);
    expect(line).toMatch(/^encoding\.tooltip: 'value' is aggregated as mean on y — /);
  });

  it("refuses it in a layer too", () => {
    const layered =
      `${base}layer:\n  - mark: line\n    encoding:\n      x: {field: item, type: nominal}\n` +
      "      y: {field: value, type: quantitative, aggregate: sum}\n" +
      "      size: {field: value, type: quantitative, aggregate: count}\n";
    const [line] = verdict(layered);
    expect(line).toMatch(/^layer\[0\]\.encoding\.size: 'value' is aggregated as sum on y — /);
  });

  it.each([
    [", aggregate: mean", ", aggregate: mean"],
    [", aggregate: mean", ""],
    ["", ", aggregate: max"],
    ["", ""],
  ])("does not refuse one field aggregated one way (y%s, tooltip%s)", (y, tip) => {
    expect(verdict(twoOps(y, tip))).toEqual([]);
  });

  // #847/#848 PR 5 P42 row 31: a stack sums its value channel when it names no
  // aggregate, so a tooltip mean of that field showed sums under a "mean"
  // label. test_spec_messages.py reads the same.
  const stackTip = (y: string, tip: string, horizontal = false) => {
    const [slot, value] = horizontal ? ["y", "x"] : ["x", "y"];
    return (
      `${base}mark: {type: bar, stack: true}\nencoding:\n  ${slot}: {field: item, type: nominal}\n` +
      `  ${value}: {field: value, type: quantitative${y}}\n  tooltip: [{field: value, type: quantitative${tip}}]\n`
    );
  };

  it("counts a stack's own sum as its value's op", () => {
    expect(verdict(stackTip("", ", aggregate: mean"))).toEqual([
      "encoding.tooltip[0]: 'value' is aggregated as sum on y — a field has one " +
        "aggregate in a layer, so mean here would show the sum: use sum here too, " +
        "or compute both in a transform aggregate, each under its own name (as:)",
    ]);
    expect(verdict(stackTip("", ", aggregate: max", true))[0]).toMatch(/^encoding\.tooltip\[0\]: 'value' is aggregated as sum on x — /);
    expect(verdict(stackTip(", aggregate: mean", ", aggregate: sum"))[0]).toMatch(
      /^encoding\.tooltip\[0\]: 'value' is aggregated as mean on y — /,
    );
  });

  it.each([
    ["", ", aggregate: sum"],
    ["", ""],
    [", aggregate: mean", ", aggregate: mean"],
  ])("does not refuse a stack's tooltip with its own op (y%s, tooltip%s)", (y, tip) => {
    expect(verdict(stackTip(y, tip))).toEqual([]);
    expect(verdict(stackTip(y, tip, true))).toEqual([]);
  });

  // #847/#848 PR 5 P41 row 21 [user, 2026-09-26]: a stack links by its slot
  // and colour only. A segment is the sum of its rows, so it has no single
  // value of any other field. test_spec_messages.py reads the same.
  const stackedBar =
    "mark: {type: bar, stack: true}\nencoding:\n  x: {field: item, type: nominal}\n" +
    "  y: {field: value, type: quantitative}\n  color: {field: group, type: nominal}\n" +
    "  tooltip: {field: region, type: nominal}\n";
  const links = "a stack links by its slot and colour only ('item', 'group') — a segment is the sum of its rows";

  it("refuses a stack keyed by another field, saying why", () => {
    expect(verdict(`${base}keys: [item, id, group, region]\n${stackedBar}`)).toEqual([
      `keys: ${links}, so it has no single 'id', 'region': key the view by its slot and colour, or drop stack so single rows link`,
    ]);
  });

  it("refuses a stack highlighting another field, saying why", () => {
    expect(verdict(`${base}highlight: {where: "region == 'n' and value > 3"}\n${stackedBar}`)).toEqual([
      `highlight.where: ${links}, so it has no single 'region': test its slot and colour, ` +
        "or its value 'value' (each segment's sum), or drop stack so single rows light",
    ]);
    const [line] = verdict(`${base}highlight: {values: {item: [p], id: [r1]}}\n${stackedBar}`);
    expect(line!.startsWith(`highlight.values: ${links}, so it has no single 'id': `)).toBe(true);
  });

  // #847/#848 PR 5 P42 row 33: "the sum of its rows" was false for a stack
  // whose value names its own aggregate. test_spec_messages.py reads the same.
  it("names the op a stack's segments are", () => {
    const mean = stackedBar.replace("type: quantitative}", "type: quantitative, aggregate: mean}");
    const meanLinks = links.replace("the sum of its rows", "the mean of its rows");
    expect(verdict(`${base}keys: [id]\n${mean}`)).toEqual([
      `keys: ${meanLinks}, so it has no single 'id': key the view by its slot and colour, or drop stack so single rows link`,
    ]);
    expect(verdict(`${base}highlight: {where: "region == 'n'"}\n${mean}`)).toEqual([
      `highlight.where: ${meanLinks}, so it has no single 'region': test its slot and colour, ` +
        "or its value 'value' (each segment's mean), or drop stack so single rows light",
    ]);
  });

  it("names a layer's stack", () => {
    const layered =
      `${base}keys: [id]\nlayer:\n  - mark: {type: area, stack: true}\n    encoding:\n` +
      "      x: {field: item, type: nominal}\n      y: {field: value, type: quantitative}\n" +
      "  - mark: scatter\n    encoding:\n      x: {field: item, type: nominal}\n" +
      "      y: {field: value, type: quantitative}\n";
    expect(verdict(layered)).toEqual([
      "keys: a stack (layer[0]) links by its slot and colour only ('item') — a segment is the sum of its rows, " +
        "so it has no single 'id': key the view by its slot and colour, or drop stack so single rows link",
    ]);
  });

  it("links a horizontal stack by its y", () => {
    const horizontal =
      "mark: {type: bar, stack: true}\nencoding:\n  x: {field: value, type: quantitative}\n  y: {field: item, type: ordinal}\n";
    const [line] = verdict(`${base}keys: [group]\n${horizontal}`);
    expect(line).toContain("only ('item')");
    expect(line).toContain("no single 'group'");
    expect(verdict(`${base}keys: [item]\n${horizontal}`)).toEqual([]);
  });

  it.each([
    "keys: [item, group]\n",
    "keys: [group]\n",
    "highlight: {where: \"value > 10 and group == 'a'\"}\n",
    "highlight: {where: \"`item` == 'p' and index > 0\"}\n",
    "highlight: {values: {item: [p], value: [13]}}\n",
  ])("does not refuse a stack with %s", (top) => {
    expect(verdict(base + top + stackedBar)).toEqual([]);
  });

  it.each(["{type: bar, stack: false}", "bar", "{type: line, stack: true}"])("links single rows (%s) by any field", (mark) => {
    const text = `${base}keys: [id]\nhighlight: {where: "region == 'n'"}\n${stackedBar.replace("{type: bar, stack: true}", mark)}`;
    expect(verdict(text)).toEqual([]);
  });
});

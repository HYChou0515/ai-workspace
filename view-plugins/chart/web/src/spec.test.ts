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
});

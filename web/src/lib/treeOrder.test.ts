import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { compareTreePaths } from "./treeOrder";

// The SAME cases the backend asserts (tests/kb/test_tree_order.py): the order
// users see in the file tree is the order the context walk follows, and a
// change on either side reddens the other.
const HERE = dirname(fileURLToPath(import.meta.url));
const GOLDEN = resolve(HERE, "../../../tests/kb/tree_order.golden.json");

type Case = { name: string; input: string[]; expected: string[] };
const cases: Case[] = JSON.parse(readFileSync(GOLDEN, "utf-8")).cases;

describe("tree order (golden, shared with the backend)", () => {
  it("has cases", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  for (const c of cases) {
    it(c.name, () => {
      expect([...c.input].sort(compareTreePaths)).toEqual(c.expected);
      // No dependence on the incoming order.
      expect([...c.input].reverse().sort(compareTreePaths)).toEqual(c.expected);
    });
  }
});

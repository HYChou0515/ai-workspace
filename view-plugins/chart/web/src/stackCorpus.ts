/**
 * Test helper: the stacks of `wire-corpus/stack-sums.json`, each the sandbox's
 * real answer for a stack of raw rows (summed per slot and colour, #847/#848
 * PR 5 P40 row 18) and pandas' own stack tops. Written by the sandbox's
 * `scripts/write_stack_corpus.py`, held current by its test_stack_sum.py.
 * A test that draws a stack of rows reads its answer here rather than
 * summing rows by hand: the sum is the sandbox's, once.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import type { Answer } from "./option";

/** `tops[k][j]`: where colour k's stack ends at slot j (both sorted, a
 * missing slot last); null on a log axis. `data` is the raw rows. */
export type StackCase = {
  name: string;
  data: Record<string, (string | number | null)[]>;
  spec: Record<string, unknown> & { keys: string[] };
  answer: Answer;
  tops: number[][] | null;
};

const file = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "wire-corpus", "stack-sums.json");

export const stackCases: StackCase[] = (JSON.parse(readFileSync(file, "utf8")) as { cases: StackCase[] }).cases;

export function stackCase(name: string): StackCase {
  const c = stackCases.find((k) => k.name === name);
  if (!c) throw new Error(`no stack case named ${JSON.stringify(name)}`);
  return c;
}

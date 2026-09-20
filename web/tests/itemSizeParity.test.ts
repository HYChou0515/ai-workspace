/**
 * The size fields' refusals, graded against the server's answer sheet (#830).
 *
 * `cpuFault` / `memoryFault` claim to refuse on the keystroke exactly what
 * `PUT /resources` refuses with a 422 — a value past `_MAX_CORES` /
 * `_MAX_BYTES` included, read from the record rather than held here. The
 * sheet, `tests/fixtures/item_size_parity.json`, is that route's own verdict
 * on each input: `tests/quota/test_item_size_parity.py` asserts every row and
 * both ceilings against the running server, so a stale sheet fails THERE
 * (python), and this file asks only whether the client agrees with the sheet.
 * Two implementations kept alike by hand is what the issue forbids; this is
 * one oracle and two graded copies.
 *
 * OUTSIDE `src/` on purpose: the web image builds from `COPY web/ ./` alone
 * (docker/Dockerfile), so `tests/fixtures/` is not there, and `tsconfig`
 * compiles `include: ["src"]`. See `shippedWuiExample.test.ts`.
 */
import { describe, expect, it } from "vitest";

import sheet from "../../tests/fixtures/item_size_parity.json";
import { cpuFault, memoryFault, normaliseMemory } from "../src/components/ItemEnvironmentSize";

describe("the size fields agree with the server's answer sheet", () => {
  it("cpu: what would be sent is the sheet's number, and it is refused here iff the server refuses it", () => {
    for (const row of sheet.cpu) {
      expect(Number(row.typed), row.typed).toBe(row.sent);
      expect(cpuFault(row.typed, sheet.max_cpu_cores) === null, row.typed).toBe(row.accepted);
    }
  });

  it("memory: what would be sent is the sheet's spelling, and it is refused here iff the server refuses it", () => {
    for (const row of sheet.memory) {
      expect(normaliseMemory(row.typed), row.typed).toBe(row.sent);
      expect(memoryFault(row.typed, sheet.max_memory_bytes) === null, row.typed).toBe(row.accepted);
    }
  });

  it("the sheet can fail: a refusal past the ceiling is `over` here, not `unreadable`", () => {
    // Without this a client that read every number as garbage would agree
    // with every refusal on the sheet, and the ceiling would be untested.
    const cpuOver = sheet.cpu.filter((r) => !r.accepted && r.sent > 0);
    const memOver = sheet.memory.filter((r) => !r.accepted && r.sent !== null);
    expect(cpuOver.length).toBeGreaterThan(0);
    expect(memOver.length).toBeGreaterThan(0);
    for (const r of cpuOver) expect(cpuFault(r.typed, sheet.max_cpu_cores), r.typed).toBe("over");
    for (const r of memOver) expect(memoryFault(r.typed, sheet.max_memory_bytes), r.typed).toBe("over");
  });
});

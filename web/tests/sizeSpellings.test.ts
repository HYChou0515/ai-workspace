/**
 * The client reads each size spelling as the bytes the server reads it as.
 *
 * The sheet, `tests/fixtures/size_spellings.json`, is graded against the
 * server's `parse_size` in `tests/quota/test_size_spellings.py`; this file asks
 * only whether the client agrees. Outside `src/` for the reason
 * `itemSizeParity.test.ts` gives: the web image does not carry `tests/fixtures/`.
 */
import { describe, expect, it } from "vitest";

import sheet from "../../tests/fixtures/size_spellings.json";
import { normaliseMemory, parseSize } from "../src/components/ItemEnvironmentSize";

describe("the client reads sizes as the server does", () => {
  it("every row of the sheet", () => {
    for (const row of sheet.rows) {
      expect(parseSize(normaliseMemory(row.typed)), row.typed).toBe(row.bytes);
    }
  });
});

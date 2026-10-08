/**
 * The chat's copy of `agent.outside_lookup.declared_lookup` — the turn stops
 * when the BACKEND sees a "請幫我查" card, so a reply the chat reads
 * differently is a turn stopped for nothing on screen. Held to the table the
 * backend generates and re-checks (`tests/fixtures/outside_lookup_cases.json`).
 *
 * In `web/tests/`, not beside the module: the fixtures live outside `web/`,
 * which the image build never copies (`importBoundary.test.ts`).
 */
import { describe, expect, it } from "vitest";

import table from "../../tests/fixtures/outside_lookup_cases.json";

import { parseOutsideLookup } from "../src/renderers/outsideLookup";

describe("parseOutsideLookup", () => {
  it.each(table.cases)("agrees with declared_lookup: %j", (c) => {
    expect(parseOutsideLookup(c.output)).toEqual(c.card);
  });
});

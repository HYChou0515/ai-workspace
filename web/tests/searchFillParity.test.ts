/**
 * The card's half of one target, one address (docs/plan-lookup-target-choice.md
 * §3.4): the card opens `searchUrl(target, query)` by hand, the helper is handed
 * the backend's `fill_target` — both held to `tests/fixtures/search_fill_cases.json`
 * (the backend's half: `tests/api/test_outside_job_targets.py`).
 *
 * In `web/tests/`, not beside the module: the fixtures live outside `web/`,
 * which the image build never copies (`importBoundary.test.ts`).
 */
import { describe, expect, it } from "vitest";

import table from "../../tests/fixtures/search_fill_cases.json";

import { searchUrl } from "../src/api/outsideLookup";

describe("searchUrl", () => {
  it("has cases to agree on", () => {
    expect(table.cases.length).toBeGreaterThanOrEqual(5);
  });

  it.each(table.cases)("fills like the backend: %j", (c) => {
    expect(searchUrl({ name: "t", url: c.url }, c.query)).toBe(c.filled);
  });
});

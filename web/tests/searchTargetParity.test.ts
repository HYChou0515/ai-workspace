/**
 * The card's copy of the helper's `pick_search_target`
 * (ai-workspace-helper/src/ai_workspace_helper/builtin_errands.py): the
 * helper's search button names the destination the helper will use
 * (docs/plan-ext-ai.md D8), so the two must pick the same one. Held to the
 * table the helper's own test re-checks (`tests/fixtures/search_target_cases.json`);
 * `null` there is "the helper has none to use".
 *
 * In `web/tests/`, not beside the module: the fixtures live outside `web/`.
 */
import { describe, expect, it } from "vitest";

import table from "../../tests/fixtures/search_target_cases.json";

import { pickSearchTarget } from "../src/api/outsideLookup";

describe("pickSearchTarget", () => {
  it.each(table.cases)("agrees with pick_search_target: %j", (c) => {
    expect(pickSearchTarget(c.targets)?.name ?? null).toBe(c.picked);
  });
});

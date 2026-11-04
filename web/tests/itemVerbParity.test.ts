/**
 * The browser's copy of `perm.authorize` for an item (`hasItemVerb`) — used to
 * decide which buttons to draw. Held to the table the backend generates and
 * re-checks (`tests/fixtures/item_verb_cases.json`), so an implication added on
 * one side (docs/plan-wui-user.md D9/D10: converse and execute imply use_wui;
 * use_wui implies read_content) cannot be missing on the other.
 *
 * In `web/tests/`, not beside the module: the fixtures live outside `web/`.
 */
import { describe, expect, it } from "vitest";

import table from "../../tests/fixtures/item_verb_cases.json";

import { hasItemVerb, type ItemPermission, type ItemVerb } from "../src/lib/itemPermission";

type Case = { granted: string[]; answers: Record<string, boolean> };

describe("hasItemVerb", () => {
  it.each(table.cases as Case[])("agrees with authorize: %j", (c) => {
    const perm: ItemPermission = { visibility: "restricted" };
    for (const verb of c.granted) (perm as Record<string, unknown>)[verb] = ["user:alice"];
    for (const [verb, want] of Object.entries(c.answers)) {
      expect([verb, hasItemVerb(perm, "alice", "bob", verb as ItemVerb, false)]).toEqual([verb, want]);
    }
  });
});

/**
 * The FE's copy of `api.env_layers.resolve_env` — used only to LABEL whose
 * value a tool will get. Held to the table the backend generates and re-checks
 * (`tests/fixtures/env_layers_cases.json`), so the two rules cannot drift by
 * hand: a label that disagreed with what the tool really receives would be
 * the panel lying about a credential.
 */
import { describe, expect, it } from "vitest";

import table from "../../../tests/fixtures/env_layers_cases.json";

import { layerInUse } from "./envLayers";

type Case = {
  shared: Record<string, string>;
  private: Record<string, string>;
  policy: Record<string, string>;
  expected: string;
};

describe("layerInUse", () => {
  it.each(table.cases as Case[])("agrees with resolve_env: %j", (c) => {
    expect(layerInUse("K", c.shared, c.private, c.policy)).toBe(c.expected);
  });
});

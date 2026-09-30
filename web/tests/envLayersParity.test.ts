/**
 * The FE's copy of `api.env_layers.resolve_env` — used only to LABEL whose
 * value a tool will get. Held to the table the backend generates and re-checks
 * (`tests/fixtures/env_layers_cases.json`), so the two rules cannot drift by
 * hand: a label that disagreed with what the tool really receives would be
 * the panel lying about a credential.
 *
 * In `web/tests/`, not beside the module: the fixtures live outside `web/`,
 * which the image build never copies (`importBoundary.test.ts`).
 */
import { describe, expect, it } from "vitest";

import table from "../../tests/fixtures/env_layers_cases.json";

import { layerInUse } from "../src/lib/envLayers";

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

import ownCases from "../../tests/fixtures/private_layer_cases.json";

import { ownLayer } from "../src/lib/envLayers";

type OwnCase = {
  typed: Record<string, string>;
  seam: Record<string, string>;
  expected: [string, string][];
};

describe("ownLayer", () => {
  // Review round 2: the FE composed a person's layer by hand in four places,
  // held to nothing. The backend's `own_layer` generates and re-checks this
  // table; entries (not the dict) because the order is part of it.
  it.each(ownCases.cases as unknown as OwnCase[])("agrees with own_layer: %j", (c) => {
    expect(Object.entries(ownLayer(c.typed, c.seam))).toEqual(c.expected);
  });
});

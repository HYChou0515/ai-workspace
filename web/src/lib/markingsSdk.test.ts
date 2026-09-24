/**
 * #847 PR 3: a runtime plugin reaches named markings through `@aiws/view-sdk`,
 * and must get the HOST's own hook — a copy would read a different context and
 * link to nothing.
 */
import * as sdk from "@aiws/view-sdk";
import { describe, expect, it } from "vitest";

import { useMarking } from "../hooks/useMarking";
import { isLit, projectOntoKeys } from "./markings";

describe("@aiws/view-sdk markings", () => {
  it("exports the host's own useMarking, isLit and projectOntoKeys", () => {
    expect(sdk.useMarking).toBe(useMarking);
    expect(sdk.isLit).toBe(isLit);
    expect(sdk.projectOntoKeys).toBe(projectOntoKeys);
  });
});

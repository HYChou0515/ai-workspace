/**
 * #847 PR 3: a runtime plugin reaches named markings through `@aiws/view-sdk`,
 * and must get the HOST's own hook — a copy would read a different context and
 * link to nothing.
 */
// The module the import map (and vitest's alias) points `@aiws/view-sdk` at.
import * as sdk from "../renderers/entity/public";
import { describe, expect, it } from "vitest";

import { useMarking } from "../hooks/useMarking";
import { isLit, markedBy, markingFrom, markingRows, markingSize, projectOntoKeys } from "./markings";

describe("@aiws/view-sdk markings", () => {
  it("exports the host's own useMarking, isLit and projectOntoKeys", () => {
    expect(sdk.useMarking).toBe(useMarking);
    expect(sdk.isLit).toBe(isLit);
    expect(sdk.projectOntoKeys).toBe(projectOntoKeys);
    // #861: the tuple shape's own constructors, so a plugin never assembles the text.
    expect(sdk.markingFrom).toBe(markingFrom);
    expect(sdk.markingRows).toBe(markingRows);
    expect(sdk.markingSize).toBe(markingSize);
    expect(sdk.markedBy).toBe(markedBy);
  });
});

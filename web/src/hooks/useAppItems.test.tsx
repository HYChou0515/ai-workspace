// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useAppItems } from "./useResources";

vi.mock("../api", () => ({ api: { listAppItems: vi.fn() } }));
import { api } from "../api";

describe("useAppItems", () => {
  // #383: the app homepage lists work items, and users expect the ones they
  // touched most recently on top. specstar's autocrud list returns creation
  // order when no `sorts` is passed, so the read path must request the
  // updated_time-desc meta sort explicitly.
  it("requests the app's items sorted by updated_time descending", async () => {
    vi.mocked(api.listAppItems).mockResolvedValue([]);
    const qc = makeTestQueryClient();
    renderHook(() => useAppItems("rca", "/rca-investigation"), {
      wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });

    await waitFor(() => expect(api.listAppItems).toHaveBeenCalled());
    expect(api.listAppItems).toHaveBeenCalledWith("/rca-investigation", {
      sorts: JSON.stringify([{ type: "meta", key: "updated_time", direction: "-" }]),
    });
  });
});

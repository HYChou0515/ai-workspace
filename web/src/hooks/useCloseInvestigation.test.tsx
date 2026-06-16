// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useCloseInvestigation } from "./useInvestigationMutations";

vi.mock("../api", () => ({ api: { closeInvestigation: vi.fn() } }));
import { api } from "../api";

describe("useCloseInvestigation", () => {
  it("closes via the per-App route POST /a/{slug}/items/{id}/close", async () => {
    vi.mocked(api.closeInvestigation).mockResolvedValue(undefined);
    const qc = makeTestQueryClient();
    const { result } = renderHook(() => useCloseInvestigation("rca", "rca-investigation/1"), {
      wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });

    result.current.mutate("resolved");

    await waitFor(() => expect(api.closeInvestigation).toHaveBeenCalled());
    expect(api.closeInvestigation).toHaveBeenCalledWith("rca", "rca-investigation/1", "resolved");
  });
});

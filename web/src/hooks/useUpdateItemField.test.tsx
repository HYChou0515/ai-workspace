// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppItem } from "../api/types";
import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useUpdateItemField } from "./useResources";

vi.mock("../api", () => ({ api: { updateAppItem: vi.fn() } }));
import { api } from "../api";

const item = {
  resource_id: "rca-investigation/1",
  title: "Oven drift",
  owner: "alice",
  severity: "P2",
  product: "MX-7",
} as AppItem;

describe("useUpdateItemField", () => {
  it("PUTs the whole item with the one changed field to its resource route", async () => {
    vi.mocked(api.updateAppItem).mockResolvedValue({ resource_id: item.resource_id });
    const qc = makeTestQueryClient();
    const { result } = renderHook(
      () => useUpdateItemField("rca", "/rca-investigation", item),
      { wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap> },
    );

    result.current.setField("severity", "P0");

    await waitFor(() => expect(api.updateAppItem).toHaveBeenCalled());
    expect(api.updateAppItem).toHaveBeenCalledWith("/rca-investigation", "rca-investigation/1", {
      ...item,
      severity: "P0",
    });
  });
});

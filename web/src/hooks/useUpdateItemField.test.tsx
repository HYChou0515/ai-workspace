// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppItem } from "../api/types";
import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useUpdateItemField } from "./useResources";

vi.mock("../api", () => ({ api: { patchAppItemFields: vi.fn() } }));
import { api } from "../api";

const item = {
  resource_id: "rca-investigation/1",
  title: "Oven drift",
  owner: "alice",
  created_time: "2026-06-15T08:00:00Z",
  created_by: "alice",
  severity: "P2",
  product: "MX-7",
  permission: { visibility: "private" },
} as unknown as AppItem;

function render() {
  const qc = makeTestQueryClient();
  return renderHook(() => useUpdateItemField("rca", "/rca-investigation", item), {
    wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
  });
}

// This hook used to send `{...item, ...patch}` — the WHOLE cached item — to a
// replace-semantics PUT. Editing one field therefore rewrote every field from a
// snapshot that could be minutes old, reverting anyone else's concurrent change,
// and (because `permission` was stripped from that full body and an omitted
// field is stored as its default) turned a private item public. Send the diff.
describe("useUpdateItemField", () => {
  it("sends ONLY the changed field, never the rest of the cached item", async () => {
    vi.mocked(api.patchAppItemFields).mockResolvedValue({ resource_id: item.resource_id });
    const { result } = render();

    result.current.setField("severity", "P0");

    await waitFor(() => expect(api.patchAppItemFields).toHaveBeenCalled());
    expect(api.patchAppItemFields).toHaveBeenCalledWith(
      "/rca-investigation",
      "rca-investigation/1",
      { severity: "P0" },
    );
  });

  it("sends only the form's own fields when several change at once", async () => {
    vi.mocked(api.patchAppItemFields).mockResolvedValue({ resource_id: item.resource_id });
    const { result } = render();

    result.current.setFields({ title: "New", product: "MX-9" });

    await waitFor(() => expect(api.patchAppItemFields).toHaveBeenCalled());
    expect(api.patchAppItemFields).toHaveBeenCalledWith(
      "/rca-investigation",
      "rca-investigation/1",
      { title: "New", product: "MX-9" },
    );
  });

  // The regression that started this: open Edit, change the title, Save — and the
  // item's access must be exactly what it was.
  it("never mentions permission, so a settings save cannot change access", async () => {
    vi.mocked(api.patchAppItemFields).mockResolvedValue({ resource_id: item.resource_id });
    const { result } = render();

    result.current.setFields({ title: "New" });

    await waitFor(() => expect(api.patchAppItemFields).toHaveBeenCalled());
    const sent = vi.mocked(api.patchAppItemFields).mock.calls.at(-1)![2];
    expect(sent).not.toHaveProperty("permission");
  });
});

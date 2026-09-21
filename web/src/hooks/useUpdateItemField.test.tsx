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
  // The tool picker awaits `onSave` and THEN invalidates its rows so the reopened
  // modal reads what was just saved. `mutate` returns void, so the await was
  // over before the PATCH had left — the refetch raced the write, cached the
  // PRE-save rows, and reopening within staleTime showed the change as
  // "didn't take" (the #306 shape, on the tool picker; seen in a real-browser
  // demo of per-command pins). The promise settles when the request has.
  it("setField resolves only after the PATCH has settled", async () => {
    let finish!: (v: { resource_id: string }) => void;
    vi.mocked(api.patchAppItemFields).mockReturnValue(
      new Promise<{ resource_id: string }>((res) => {
        finish = res;
      }),
    );
    const { result } = render();

    let settled = false;
    const p = Promise.resolve(result.current.setField("severity", "P0")).then(() => {
      settled = true;
    });
    await waitFor(() => expect(api.patchAppItemFields).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(settled).toBe(false); // the request is still in flight

    finish({ resource_id: item.resource_id });
    await p;
    expect(settled).toBe(true);
  });

  it("setField settles (never rejects) when the PATCH fails", async () => {
    vi.mocked(api.patchAppItemFields).mockRejectedValue(new Error("403"));
    const { result } = render();

    await expect(Promise.resolve(result.current.setField("severity", "P0"))).resolves.toBeUndefined();
  });
});

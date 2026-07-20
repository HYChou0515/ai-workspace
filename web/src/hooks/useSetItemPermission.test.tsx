// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { qk } from "../api/queryKeys";
import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useSetItemPermission } from "./useResources";

vi.mock("../api", () => ({ api: { setItemPermission: vi.fn() } }));
import { api } from "../api";

describe("useSetItemPermission", () => {
  // #306 PR3: the share dialog used to `await api.setItemPermission(...)` inline
  // and close. Nothing invalidated the item cache, so the workspace kept serving
  // the PRE-share permission — reopening "Manage access…" showed the OLD state,
  // which reads to the user as "the change didn't take". Invalidate both the item
  // and the list (visibility can now hide/reveal the row).
  it("invalidates the item + list caches after a successful set", async () => {
    vi.mocked(api.setItemPermission).mockResolvedValue({ visibility: "restricted", notified: [] });
    const qc = makeTestQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");

    const { result } = renderHook(() => useSetItemPermission("rca", "INC-1"), {
      wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });
    result.current.setPermission({ visibility: "restricted", read_chat: ["user:bob"] });

    await waitFor(() => expect(api.setItemPermission).toHaveBeenCalled());
    expect(api.setItemPermission).toHaveBeenCalledWith("rca", "INC-1", {
      visibility: "restricted",
      read_chat: ["user:bob"],
    });
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: qk.appItem("rca", "INC-1") }),
    );
    expect(spy).toHaveBeenCalledWith({ queryKey: qk.appItems("rca") });
  });

  // A 403 from the setter must surface, not vanish into an unhandled rejection
  // that leaves the dialog hanging open with no feedback.
  it("exposes the failure instead of swallowing it", async () => {
    vi.mocked(api.setItemPermission).mockRejectedValue(new Error("not authorized"));
    const qc = makeTestQueryClient();

    const { result } = renderHook(() => useSetItemPermission("rca", "INC-1"), {
      wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });
    result.current.setPermission({ visibility: "private" });

    await waitFor(() => expect(result.current.error).toBe("not authorized"));
  });
});

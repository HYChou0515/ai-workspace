// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../api/types";
import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useItemCanWrite } from "./useItemCanWrite";

vi.mock("../api", () => ({
  api: { getAppManifest: vi.fn(), getAppItem: vi.fn(), getCurrentUser: vi.fn() },
}));
import { api } from "../api";

const manifest = { resource_route: "/pm-project" } as AppManifest;

function makeItem(overrides: Partial<AppItem>): AppItem {
  return {
    resource_id: "pm-project/1",
    title: "Launch",
    owner: "alice",
    created_time: "2026-07-01T00:00:00Z",
    created_by: "alice",
    ...overrides,
  } as AppItem;
}

function renderCanWrite(currentUser: string) {
  vi.mocked(api.getAppManifest).mockResolvedValue(manifest);
  vi.mocked(api.getCurrentUser).mockResolvedValue(currentUser);
  const qc = makeTestQueryClient();
  return renderHook(() => useItemCanWrite("pm", "pm-project/1"), {
    wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
  });
}

describe("useItemCanWrite (#455 §E)", () => {
  it("is optimistically writable while the item is still loading", () => {
    vi.mocked(api.getAppManifest).mockResolvedValue(manifest);
    vi.mocked(api.getAppItem).mockReturnValue(new Promise(() => {})); // never resolves
    vi.mocked(api.getCurrentUser).mockResolvedValue("bob");
    const qc = makeTestQueryClient();
    const { result } = renderHook(() => useItemCanWrite("pm", "pm-project/1"), {
      wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });
    expect(result.current).toBe(true);
  });

  it("lets the owner write a private item", async () => {
    vi.mocked(api.getAppItem).mockResolvedValue(
      makeItem({ created_by: "alice", permission: { visibility: "private" } }),
    );
    const { result } = renderCanWrite("alice");
    await waitFor(() => expect(result.current).toBe(true));
  });

  it("denies a non-owner on a private item", async () => {
    vi.mocked(api.getAppItem).mockResolvedValue(
      makeItem({ created_by: "alice", permission: { visibility: "private" } }),
    );
    const { result } = renderCanWrite("bob");
    await waitFor(() => expect(result.current).toBe(false));
  });

  it("allows a non-owner on a public item", async () => {
    vi.mocked(api.getAppItem).mockResolvedValue(
      makeItem({ created_by: "alice", permission: { visibility: "public" } }),
    );
    const { result } = renderCanWrite("bob");
    await waitFor(() => expect(result.current).toBe(true));
  });

  it("allows a restricted item's granted writer", async () => {
    vi.mocked(api.getAppItem).mockResolvedValue(
      makeItem({
        created_by: "alice",
        permission: { visibility: "restricted", edit_content: ["user:bob"] },
      }),
    );
    const { result } = renderCanWrite("bob");
    await waitFor(() => expect(result.current).toBe(true));
  });
});

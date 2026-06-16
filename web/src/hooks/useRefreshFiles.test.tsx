// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import { FileBufferProvider, FileBufferStore } from "./fileBuffer";
import { useRefreshFiles } from "./useRefreshFiles";
import { WorkspaceSlugProvider } from "./useWorkspaceSlug";

afterEach(() => vi.restoreAllMocks());

function wrapper(client: QueryClient, store: FileBufferStore) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <WorkspaceSlugProvider value="rca">
        <FileBufferProvider store={store}>{children}</FileBufferProvider>
      </WorkspaceSlugProvider>
    </QueryClientProvider>
  );
}

describe("useRefreshFiles", () => {
  it("server-flushes, invalidates list+dirs+content, reloads clean buffers", async () => {
    const id = "inv-x";
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    // Pre-populate the caches so we can verify they were invalidated.
    client.setQueryData(qk.files(id), { items: [], dirs: [] });
    client.setQueryData(qk.dirs(id), []);
    client.setQueryData(qk.file(id, "/a.md"), { text: "old" });
    client.setQueryData(qk.file(id, "/b.md"), { text: "old-b" });
    // Pre-populate the editor buffer for one path so reload should fire.
    const store = new FileBufferStore({
      readFile: vi.fn(async () => ({
        kind: "text" as const,
        path: "/a.md",
        size: 3,
        text: "new",
        encoding: "utf-8" as const,
      })),
      writeFile: vi.fn(async () => {}),
    });
    store.ensureLoaded("/a.md");
    // Let the initial load settle.
    await new Promise((r) => setTimeout(r, 0));

    const refreshSpy = vi.spyOn(api, "refreshFiles").mockResolvedValue(undefined);
    const reloadSpy = vi.spyOn(store, "reload");

    const { result } = renderHook(() => useRefreshFiles(id), {
      wrapper: wrapper(client, store),
    });
    await act(async () => {
      await result.current();
    });

    // 1. Server flush called.
    expect(refreshSpy).toHaveBeenCalledWith("rca", id);
    // 2. All three cache families invalidated (state stale).
    expect(client.getQueryState(qk.files(id))?.isInvalidated).toBe(true);
    expect(client.getQueryState(qk.dirs(id))?.isInvalidated).toBe(true);
    expect(client.getQueryState(qk.file(id, "/a.md"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(qk.file(id, "/b.md"))?.isInvalidated).toBe(true);
    // 3. Editor buffer was reloaded.
    expect(reloadSpy).toHaveBeenCalledWith("/a.md");
  });

  it("skips reloading buffers with unsaved (dirty) edits", async () => {
    const id = "inv-x";
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const store = new FileBufferStore({
      readFile: vi.fn(async () => ({
        kind: "text" as const,
        path: "/dirty.md",
        size: 3,
        text: "saved",
        encoding: "utf-8" as const,
      })),
      writeFile: vi.fn(async () => {}),
    });
    store.ensureLoaded("/dirty.md");
    await new Promise((r) => setTimeout(r, 0));
    // The user typed something — buffer is now dirty.
    store.setText("/dirty.md", "user typing");
    expect(store.isDirty("/dirty.md")).toBe(true);

    vi.spyOn(api, "refreshFiles").mockResolvedValue(undefined);
    const reloadSpy = vi.spyOn(store, "reload");

    const { result } = renderHook(() => useRefreshFiles(id), {
      wrapper: wrapper(client, store),
    });
    await act(async () => {
      await result.current();
    });

    // Dirty path NOT reloaded — the user's edits would be silently lost.
    expect(reloadSpy).not.toHaveBeenCalled();
    // (Still dirty — preserved.)
    expect(store.isDirty("/dirty.md")).toBe(true);
  });

  it("still invalidates the FE caches even if the server flush fails", async () => {
    const id = "inv-x";
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(qk.files(id), { items: [], dirs: [] });
    const store = new FileBufferStore({
      readFile: vi.fn(),
      writeFile: vi.fn(),
    });

    vi.spyOn(api, "refreshFiles").mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useRefreshFiles(id), {
      wrapper: wrapper(client, store),
    });
    await act(async () => {
      await result.current();
    });

    // A stale snapshot is still better than nothing — the FE cache wipe
    // alone forces a fresh read from whatever the server has.
    expect(client.getQueryState(qk.files(id))?.isInvalidated).toBe(true);
  });
});

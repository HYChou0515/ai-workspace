// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { KbApi, KbDocument, KbDocumentsStatus } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { QueryWrap, makeTestQueryClient } from "../../test/queryWrapper";
import { fetchAllDocs, useCollectionDocs } from "./useCollectionDocs";

function doc(partial: Partial<KbDocument> & { path: string }): KbDocument {
  return {
    resource_id: `id:${partial.path}`,
    content_type: "text/markdown",
    created_by: "me",
    status: "ready",
    ...partial,
  };
}

function emptyStatus(): KbDocumentsStatus {
  return { total: 0, counts: {}, runs: {}, latest_ms: 0 };
}

function stubClient(items: KbDocument[], status: KbDocumentsStatus): KbApi {
  return {
    listDocuments: vi.fn(async () => ({
      items,
      total: items.length,
      offset: 0,
      limit: 2000,
      has_more: false,
    })),
    documentsStatus: vi.fn(async () => status),
  } as unknown as KbApi;
}

describe("fetchAllDocs (#395)", () => {
  it("fetches in one big page, not 200-doc slices", async () => {
    const listDocuments = vi.fn(async (_cid: string, page?: { offset?: number; limit?: number }) => ({
      items: [] as KbDocument[],
      total: 0,
      offset: page?.offset ?? 0,
      limit: page?.limit ?? 0,
      has_more: false,
    }));
    await fetchAllDocs({ listDocuments } as unknown as Pick<KbApi, "listDocuments">, "c1");
    expect(listDocuments).toHaveBeenCalledWith("c1", { offset: 0, limit: 2000 });
  });
});

describe("useCollectionDocs (#395)", () => {
  const wrap =
    (client = makeTestQueryClient()) =>
    ({ children }: { children: ReactNode }) => <QueryWrap client={client}>{children}</QueryWrap>;

  it("merges the status endpoint's run progress into indexing rows", async () => {
    const rows = [doc({ path: "/big.pdf", status: "indexing" }), doc({ path: "/done.md" })];
    const status: KbDocumentsStatus = {
      total: 2,
      counts: { indexing: 1, ready: 1 },
      runs: { "id:/big.pdf": { units_done: 8, units_total: 24 } },
      latest_ms: 111,
    };
    const { result } = renderHook(() => useCollectionDocs("c1", stubClient(rows, status)), {
      wrapper: wrap(),
    });
    await waitFor(() => expect(result.current.docs).toHaveLength(2));
    await waitFor(() => {
      const big = result.current.docs.find((d) => d.path === "/big.pdf");
      expect([big?.units_done, big?.units_total]).toEqual([8, 24]);
    });
    // the ready row is untouched
    const done = result.current.docs.find((d) => d.path === "/done.md");
    expect(done?.units_total ?? 0).toBe(0);
  });

  it("refetches the list only when the status stamp moves", async () => {
    const rows = [doc({ path: "/a.md", status: "indexing" })];
    let status: KbDocumentsStatus = {
      total: 1,
      counts: { indexing: 1 },
      runs: {},
      latest_ms: 100,
    };
    const client = {
      listDocuments: vi.fn(async () => ({
        items: rows,
        total: 1,
        offset: 0,
        limit: 2000,
        has_more: false,
      })),
      documentsStatus: vi.fn(async () => status),
    } as unknown as KbApi;
    const qc = makeTestQueryClient();
    const { result } = renderHook(() => useCollectionDocs("c1", client), { wrapper: wrap(qc) });
    await waitFor(() => expect(result.current.docs).toHaveLength(1));
    await waitFor(() => expect(result.current.status).toBeDefined());
    const listCalls = (client.listDocuments as ReturnType<typeof vi.fn>).mock.calls.length;

    // A poll tick with an UNCHANGED summary must not refetch the list.
    await qc.refetchQueries({ queryKey: qk.kb.documentsStatus("c1") });
    await new Promise((r) => setTimeout(r, 20));
    expect((client.listDocuments as ReturnType<typeof vi.fn>).mock.calls.length).toBe(listCalls);

    // A tick where the stamp moved (a doc flipped) invalidates the list.
    status = { total: 1, counts: { ready: 1 }, runs: {}, latest_ms: 200 };
    await qc.refetchQueries({ queryKey: qk.kb.documentsStatus("c1") });
    await waitFor(() =>
      expect((client.listDocuments as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
        listCalls,
      ),
    );
  });

  it("reports indexingCount from the status counts, docs as fallback", async () => {
    const rows = [doc({ path: "/a.md", status: "indexing" })];
    const status: KbDocumentsStatus = {
      total: 3,
      counts: { indexing: 3 },
      runs: {},
      latest_ms: 5,
    };
    const { result } = renderHook(() => useCollectionDocs("c1", stubClient(rows, status)), {
      wrapper: wrap(),
    });
    // Before the status arrives the docs-derived count serves; then counts win.
    await waitFor(() => expect(result.current.indexingCount).toBe(3));
  });

  it("survives a client without documentsStatus (older stubs): docs still serve", async () => {
    const client = {
      listDocuments: vi.fn(async () => ({
        items: [doc({ path: "/a.md" })],
        total: 1,
        offset: 0,
        limit: 2000,
        has_more: false,
      })),
    } as unknown as KbApi;
    const { result } = renderHook(() => useCollectionDocs("c1", client), { wrapper: wrap() });
    await waitFor(() => expect(result.current.docs).toHaveLength(1));
    expect(result.current.indexingCount).toBe(0);
  });
});

describe("useCollectionDocs polling gate (#395)", () => {
  it("exposes shouldPoll=true while anything is indexing (from either source)", async () => {
    const rows = [doc({ path: "/a.md", status: "indexing" })];
    const { result } = renderHook(
      () => useCollectionDocs("c1", stubClient(rows, emptyStatus())),
      {
        wrapper: (({ children }: { children: ReactNode }) => (
          <QueryWrap>{children}</QueryWrap>
        )) as never,
      },
    );
    // status says idle but a listed row is still indexing → keep polling.
    await waitFor(() => expect(result.current.shouldPoll).toBe(true));
  });
});

describe("useCollectionDocs — polling for work that is queued but not yet visible (#569)", () => {
  const wrap =
    (client = makeTestQueryClient()) =>
    ({ children }: { children: ReactNode }) => <QueryWrap client={client}>{children}</QueryWrap>;

  it("keeps polling after a re-read is queued, even though nothing looks busy yet", async () => {
    // "Re-read all" hands the walk to a worker, so when the request answers not
    // one doc has flipped to `indexing`. `refetchInterval` is evaluated against
    // the data already in hand — so on a quiet collection it returns false and
    // the progress strip would stay dead until the user navigated away.
    vi.useFakeTimers();
    try {
      const client = stubClient([doc({ path: "/a.md" })], emptyStatus());
      const { result } = renderHook(() => useCollectionDocs("c1", client), { wrapper: wrap() });
      await vi.waitFor(() => expect(client.documentsStatus).toHaveBeenCalled());
      const quiet = vi.mocked(client.documentsStatus).mock.calls.length;

      // Nothing is indexing: without arming the window, ticks change nothing.
      await vi.advanceTimersByTimeAsync(5000);
      expect(client.documentsStatus).toHaveBeenCalledTimes(quiet);

      act(() => result.current.watchForQueuedWork());
      await vi.advanceTimersByTimeAsync(5000);

      expect(vi.mocked(client.documentsStatus).mock.calls.length).toBeGreaterThan(quiet);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling again once the watch window lapses without work appearing", async () => {
    // A run that never surfaces must not poll for ever.
    vi.useFakeTimers();
    try {
      const client = stubClient([doc({ path: "/a.md" })], emptyStatus());
      const { result } = renderHook(() => useCollectionDocs("c1", client), { wrapper: wrap() });
      await vi.waitFor(() => expect(client.documentsStatus).toHaveBeenCalled());

      act(() => result.current.watchForQueuedWork());
      await vi.advanceTimersByTimeAsync(31_000);
      const afterWindow = vi.mocked(client.documentsStatus).mock.calls.length;
      await vi.advanceTimersByTimeAsync(10_000);

      expect(client.documentsStatus).toHaveBeenCalledTimes(afterWindow);
    } finally {
      vi.useRealTimers();
    }
  });
});

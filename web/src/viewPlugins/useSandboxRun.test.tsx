// @vitest-environment happy-dom
/**
 * `useSandboxRun(plugin, cmd, args)` — a plugin's way to run one of its sandbox
 * commands for the item it is drawn in (#847/#848 PR1 P5/P6).
 */
import "@testing-library/jest-dom/vitest";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { API_PREFIX } from "../api/http";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { makeTestQueryClient, QueryWrap } from "../test/queryWrapper";
import { useSandboxRun } from "./useSandboxRun";

function wrap(slug = "pm", item = "item 1") {
  const client = makeTestQueryClient(); // one per test, shared across rerenders
  return ({ children }: { children: ReactNode }) => (
    <QueryWrap client={client}>
      <WorkspaceSlugProvider value={slug}>
        <FileServiceProvider value={investigationFileService(slug, item)}>{children}</FileServiceProvider>
      </WorkspaceSlugProvider>
    </QueryWrap>
  );
}

function answer(body: unknown, status = 200) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

describe("useSandboxRun", () => {
  it("POSTs {args} to the item's runner route and returns the command's output", async () => {
    const fetchMock = answer({ stdout: '{"rows":3}', stderr: "", exit_code: 0 });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSandboxRun("chart", "summary", { source: "a.csv" }), { wrapper: wrap() });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual({ stdout: '{"rows":3}', stderr: "", exit_code: 0 });
    expect(result.current.error).toBeNull();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API_PREFIX}/a/pm/items/item%201/view-plugins/chart/summary`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ args: { source: "a.csv" } });
  });

  it("a non-zero exit is the command's answer, not a transport error", async () => {
    vi.stubGlobal("fetch", answer({ stdout: "", stderr: "no column fail_rate", exit_code: 2 }));
    const { result } = renderHook(() => useSandboxRun("chart", "validate", {}), { wrapper: wrap() });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.exit_code).toBe(2);
    expect(result.current.error).toBeNull();
  });

  it("a refused call surfaces the server's reason as the error", async () => {
    vi.stubGlobal("fetch", answer({ detail: "args too large: pass a file path" }, 413));
    const { result } = renderHook(() => useSandboxRun("chart", "summary", {}), { wrapper: wrap() });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.message).toContain("pass a file path");
    expect(result.current.data).toBeUndefined();
  });

  it("the same call is cached; different args run again", async () => {
    const fetchMock = answer({ stdout: "x", stderr: "", exit_code: 0 });
    vi.stubGlobal("fetch", fetchMock);
    const w = wrap();
    const a = renderHook(() => useSandboxRun("chart", "summary", { k: 1 }), { wrapper: w });
    await waitFor(() => expect(a.result.current.data).toBeDefined());
    a.rerender();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("outside an item workspace it says so instead of calling a bad URL", async () => {
    const fetchMock = answer({});
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSandboxRun("chart", "summary", {}), { wrapper: wrap("", "x") });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.message).toMatch(/item workspace/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not run while disabled", async () => {
    const fetchMock = answer({ stdout: "", stderr: "", exit_code: 0 });
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useSandboxRun("chart", "summary", {}, { enabled: false }), { wrapper: wrap() });
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(false);
  });
});

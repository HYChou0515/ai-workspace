// @vitest-environment happy-dom
import type { QueryClient } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/entities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/entities")>();
  return { ...actual, entitiesApi: { create: vi.fn(), update: vi.fn() } };
});
import { entitiesApi, EntityConflictError, type EntityInstance, type EntityList } from "../api/entities";
import { qk } from "../api/queryKeys";
import { makeTestQueryClient, QueryWrap } from "../test/queryWrapper";
import { useEntityWrite, type UseEntityWriteOptions } from "./useEntityWrite";

const SLUG = "pm";
const ITEM = "item-1";
const TYPE = "issue";

const rec = (number: number, version: string, fields: Record<string, unknown> = {}): EntityInstance => ({
  number,
  type_name: TYPE,
  fields,
  body: "",
  diagnostics: [],
  version,
});

function setup(entities: EntityInstance[], options?: UseEntityWriteOptions) {
  const qc: QueryClient = makeTestQueryClient();
  qc.setQueryData<EntityList>(qk.entities.list(SLUG, ITEM, TYPE), { entities, invalid: [] });
  const { result } = renderHook(() => useEntityWrite(SLUG, ITEM, TYPE, options), {
    wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
  });
  return { qc, result };
}

describe("useEntityWrite", () => {
  beforeEach(() => vi.clearAllMocks());

  it("threads the cached record's version as expected_version on patch (§C6)", async () => {
    vi.mocked(entitiesApi.update).mockResolvedValue(rec(1, "v2", { status: "done" }));
    const { result } = setup([rec(1, "v1", { status: "open" })]);
    act(() => result.current.patch(1, { status: "done" }));
    await waitFor(() => expect(entitiesApi.update).toHaveBeenCalled());
    expect(entitiesApi.update).toHaveBeenCalledWith(SLUG, ITEM, TYPE, 1, { status: "done" }, "v1");
  });

  it("surfaces a conflicted record number on 409 instead of clobbering (§B2)", async () => {
    vi.mocked(entitiesApi.update).mockRejectedValue(new EntityConflictError());
    const { result } = setup([rec(1, "v1", { status: "open" })]);
    act(() => result.current.patch(1, { status: "done" }));
    await waitFor(() => expect(result.current.conflicts).toContain(1));
  });

  it("clears a conflict via dismissConflict", async () => {
    vi.mocked(entitiesApi.update).mockRejectedValue(new EntityConflictError());
    const { result } = setup([rec(1, "v1")]);
    act(() => result.current.patch(1, { status: "done" }));
    await waitFor(() => expect(result.current.conflicts).toContain(1));
    act(() => result.current.dismissConflict(1));
    expect(result.current.conflicts).not.toContain(1);
  });

  it("is a no-op write when canWrite is false (read-only member, §E)", () => {
    const { result } = setup([rec(1, "v1")], { canWrite: false });
    expect(result.current.canWrite).toBe(false);
    act(() => result.current.patch(1, { status: "done" }));
    act(() => result.current.create({ title: "X" }));
    expect(entitiesApi.update).not.toHaveBeenCalled();
    expect(entitiesApi.create).not.toHaveBeenCalled();
  });

  it("creates a record through the shared write path", async () => {
    vi.mocked(entitiesApi.create).mockResolvedValue(rec(2, "v1", { title: "X" }));
    const { result } = setup([]);
    act(() => result.current.create({ title: "X" }));
    await waitFor(() => expect(entitiesApi.create).toHaveBeenCalledWith(SLUG, ITEM, TYPE, { title: "X" }));
  });
});

// @vitest-environment happy-dom
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const item = {
  resource_id: "i-1",
  owner: "olive",
  created_by: "olive",
  permission: { visibility: "restricted", converse: ["user:amy", "group:etch"] },
};
let loaded: typeof item | undefined = item;

vi.mock("./useResources", () => ({
  useAppManifest: (slug: string) => (slug ? { resource_route: "/rca-item" } : undefined),
  useAppItem: (_slug: string, route: string | undefined, id: string) => (route && id === "i-1" ? loaded : undefined),
}));

import { useItemPeople } from "./useItemPeople";

describe("useItemPeople", () => {
  it("is the people of the item at that address (owner, creator, named grants)", () => {
    loaded = item;
    const { result } = renderHook(() => useItemPeople("rca", "i-1"));
    expect([...result.current].sort()).toEqual(["amy", "olive"]);
  });

  it("is no one until the item has loaded", () => {
    loaded = undefined;
    const { result } = renderHook(() => useItemPeople("rca", "i-1"));
    expect(result.current).toEqual([]);
  });
});

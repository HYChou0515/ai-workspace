// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { User } from "../api/types";
import { QueryWrap, makeTestQueryClient } from "../test/queryWrapper";
import { useUsers } from "./useUsers";

vi.mock("../api", () => ({ api: { getUsers: vi.fn() } }));
import { api } from "../api";

const u = (id: string, section: string): User => ({
  id,
  name: id === "alice" ? "Alice Chen" : "Bob Liu",
  section,
  email: "",
  photo_url: null,
});

describe("useUsers", () => {
  it("dedupes the directory by id so picker keys stay unique (#42)", async () => {
    // a directory that lists Alice once per group → repeated id
    vi.mocked(api.getUsers).mockResolvedValue([
      u("alice", "Reflow"),
      u("alice", "SMT"),
      u("bob", "Etch"),
      u("alice", "AOI"),
    ]);
    const qc = makeTestQueryClient();
    const { result } = renderHook(() => useUsers(), {
      wrapper: ({ children }) => <QueryWrap client={qc}>{children}</QueryWrap>,
    });
    await waitFor(() => expect(result.current.length).toBeGreaterThan(0));
    // each id appears once, first occurrence wins
    expect(result.current.map((x) => x.id)).toEqual(["alice", "bob"]);
    expect(result.current.find((x) => x.id === "alice")?.section).toBe("Reflow");
  });
});

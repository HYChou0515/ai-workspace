// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import { QueryWrap } from "../test/queryWrapper";
import { UserChip } from "./UserChip";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("UserChip", () => {
  it("renders the directory name for a user id", async () => {
    vi.spyOn(api, "getUsers").mockResolvedValue([
      { id: "alice", name: "Alice Chen", section: "Reflow", email: "", photo_url: null },
    ]);
    render(
      <QueryWrap>
        <UserChip userId="alice" />
      </QueryWrap>,
    );
    await waitFor(() => expect(screen.getByText("Alice Chen")).toBeInTheDocument());
  });

  it("falls back to the bare id for an unknown user", async () => {
    vi.spyOn(api, "getUsers").mockResolvedValue([]);
    render(
      <QueryWrap>
        <UserChip userId="ghost" />
      </QueryWrap>,
    );
    await waitFor(() => expect(screen.getByText("ghost")).toBeInTheDocument());
  });
});

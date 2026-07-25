// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QueryWrap } from "../test/queryWrapper";
import { useCreateChat } from "./useCreateChat";

const createAppItem = vi.fn((..._a: unknown[]) => Promise.resolve({ resource_id: "playground-item:new" }));
vi.mock("../api", () => ({ api: { createAppItem: (...a: unknown[]) => createAppItem(...a) } }));

afterEach(() => {
  cleanup();
  createAppItem.mockClear();
});

function Harness() {
  const create = useCreateChat("playground");
  return (
    <button type="button" onClick={() => create.mutate()}>
      go
    </button>
  );
}

describe("useCreateChat", () => {
  it("creates a chat with default fields and jumps straight into it", async () => {
    render(
      <QueryWrap>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route path="/" element={<Harness />} />
            <Route path="/a/:slug/:itemId" element={<div data-testid="chat" />} />
          </Routes>
        </MemoryRouter>
      </QueryWrap>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(await screen.findByTestId("chat")).toBeInTheDocument(); // navigated into the new chat
    expect(createAppItem).toHaveBeenCalledWith("playground", { title: "New chat" });
  });
});

// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatListRail } from "./ChatListRail";

const items = [
  { resource_id: "rca-investigation/1", title: "Oven drift", owner: "u" },
  { resource_id: "rca-investigation/2", title: "Sensor noise", owner: "u" },
];
vi.mock("../hooks/useResources", () => ({
  useAppItems: () => ({ items, isPending: false }),
}));

afterEach(cleanup);

describe("ChatListRail", () => {
  it("lists the app's chats, links each to its item (slash id encoded), marks the current one", () => {
    render(
      <MemoryRouter>
        <ChatListRail slug="rca" resourceRoute="/rca-investigation" currentId="rca-investigation/1" />
      </MemoryRouter>,
    );
    expect(screen.getByText("Oven drift")).toBeInTheDocument();
    const other = screen.getByRole("link", { name: /Sensor noise/ });
    expect(other).toHaveAttribute("href", "/a/rca/rca-investigation%2F2");
    const current = screen.getByRole("link", { name: /Oven drift/ });
    expect(current).toHaveAttribute("data-active", "true");
  });
});

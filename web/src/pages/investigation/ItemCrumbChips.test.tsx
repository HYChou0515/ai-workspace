// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

import { ItemCrumbChips } from "./ItemCrumbChips";
import type { AppItem, AppManifest } from "../../api/types";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const manifest = { slug: "rca" } as unknown as AppManifest;

function renderChips(item: Partial<AppItem>) {
  return render(
    <MemoryRouter initialEntries={["/a/rca/123"]}>
      <ItemCrumbChips item={item as unknown as AppItem} manifest={manifest} />
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe("ItemCrumbChips", () => {
  it("topic chips deep-link to the App dashboard filtered by that topic", () => {
    renderChips({ topics: ["Reflow"], product: "" });
    fireEvent.click(screen.getByRole("button", { name: "Reflow" }));
    expect(screen.getByTestId("loc")).toHaveTextContent("/a/rca?topic=Reflow");
  });

  it("renders the product as plain text, not a clickable control (no filter target)", () => {
    renderChips({ topics: [], product: "MX-7" });
    expect(screen.getByText("MX-7")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "MX-7" })).toBeNull();
    expect(screen.queryByRole("link", { name: "MX-7" })).toBeNull();
  });
});

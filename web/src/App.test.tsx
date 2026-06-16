// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { AppRoutes } from "./App";
import { QueryWrap } from "./test/queryWrapper";

afterEach(cleanup);

function renderAt(path: string) {
  return render(
    <QueryWrap>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryWrap>,
  );
}

describe("AppRoutes", () => {
  it("renders the App Launcher at /", () => {
    renderAt("/");
    expect(screen.getByTestId("page-launcher")).toBeTruthy();
  });

  it("renders an App dashboard at /a/:slug", () => {
    renderAt("/a/rca");
    expect(screen.getByTestId("page-app-dashboard")).toBeTruthy();
  });

  it("renders the create flow at /a/:slug/new (static beats :itemId)", () => {
    renderAt("/a/rca/new");
    expect(screen.getByTestId("page-app-new")).toBeTruthy();
  });

  it("renders the item workspace at /a/:slug/:itemId", () => {
    renderAt("/a/rca/rca-investigation%2F1");
    expect(screen.getByTestId("page-app-workspace")).toBeTruthy();
  });

  it("falls back to the launcher for unknown paths", () => {
    renderAt("/totally-bogus");
    expect(screen.getByTestId("page-launcher")).toBeTruthy();
  });
});

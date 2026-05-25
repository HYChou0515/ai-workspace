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
  it("renders the Home page at /", () => {
    renderAt("/");
    expect(screen.getByTestId("page-home")).toBeTruthy();
  });

  it("renders the Investigation workspace at /investigations/:id", () => {
    renderAt("/investigations/INC-2026-0142");
    expect(screen.getByTestId("page-investigation")).toBeTruthy();
    // The id surfaces in the breadcrumb (or loading copy before fetch).
    expect(screen.getByText(/INC-2026-0142/)).toBeTruthy();
  });

  it("falls back to Home for unknown paths", () => {
    renderAt("/totally-bogus");
    expect(screen.getByTestId("page-home")).toBeTruthy();
  });
});

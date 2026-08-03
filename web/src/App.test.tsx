// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { AppRoutes } from "./App";
import { MY_RESOURCES_PATH } from "./components/ResourceLinkText";
import { translate } from "./lib/i18n";
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
    // /a/:slug dispatches on the manifest (chat-first → chat home); until it
    // loads — and for every ide/views App — the grid dashboard renders.
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

  // #692: every resource refusal now links here. The link spells the path via
  // MY_RESOURCES_PATH; this is the assertion that the path still resolves to
  // the page — otherwise the refusals would route people to the catch-all.
  it("renders My resources at the path every refusal links to", () => {
    renderAt(MY_RESOURCES_PATH);
    expect(screen.getByText(translate("zh-TW", "resources.loading"))).toBeTruthy();
    // An unknown path bounces to the launcher, so this is what tells the two
    // outcomes apart.
    expect(screen.queryByTestId("page-launcher")).toBeNull();
  });

  it("falls back to the launcher for unknown paths", () => {
    renderAt("/totally-bogus");
    expect(screen.getByTestId("page-launcher")).toBeTruthy();
  });
});

// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppHome } from "./AppHome";

const manifestRef: { current: { layout: { primary_surface: string } } | undefined } = { current: undefined };
vi.mock("../hooks/useResources", () => ({
  useAppManifest: () => manifestRef.current,
}));
vi.mock("./AppDashboard", () => ({ AppDashboard: () => <div data-testid="grid-dashboard" /> }));
vi.mock("./ChatFirstAppHome", () => ({ ChatFirstAppHome: () => <div data-testid="chat-home" /> }));

afterEach(cleanup);

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/a/playground"]}>
      <Routes>
        <Route path="/a/:slug" element={<AppHome />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppHome", () => {
  it("routes a chat-first App to the chat home, not the item grid", () => {
    manifestRef.current = { layout: { primary_surface: "chat" } };
    renderHome();
    expect(screen.getByTestId("chat-home")).toBeInTheDocument();
    expect(screen.queryByTestId("grid-dashboard")).not.toBeInTheDocument();
  });

  it("keeps the grid dashboard for an ide/views App", () => {
    manifestRef.current = { layout: { primary_surface: "ide" } };
    renderHome();
    expect(screen.getByTestId("grid-dashboard")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-home")).not.toBeInTheDocument();
  });
});

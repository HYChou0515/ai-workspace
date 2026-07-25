// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Landing, LAST_CHAT_KEY } from "./Landing";

vi.mock("./Launcher", () => ({ Launcher: () => <div data-testid="launcher">gallery</div> }));

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/a/:slug/:itemId" element={<div data-testid="chat">chat</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Landing", () => {
  it("shows the App gallery when there is no last chat", () => {
    renderLanding();
    expect(screen.getByTestId("launcher")).toBeInTheDocument();
  });

  it("resumes the last opened chat when one is remembered", () => {
    localStorage.setItem(LAST_CHAT_KEY, "/a/rca/rca-investigation%2F1");
    renderLanding();
    expect(screen.getByTestId("chat")).toBeInTheDocument();
    expect(screen.queryByTestId("launcher")).not.toBeInTheDocument();
  });
});

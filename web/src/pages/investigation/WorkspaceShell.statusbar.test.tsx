// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StatusBar } from "./WorkspaceShell";

afterEach(cleanup);

describe("StatusBar", () => {
  it("does not show a fake git branch / ahead-behind indicator", () => {
    // There is no git backend wired to this bar; showing `main` / `↑ 0 ↓ 0`
    // implies an integration that does not exist (#460 P1).
    const { container } = render(<StatusBar activeTab="notes.py" investigationId="i1" />);
    expect(container.textContent).not.toContain("main");
    expect(container.textContent).not.toContain("↑");
    expect(container.textContent).not.toContain("↓");
  });

  it("still renders the real status bar (language + encoding)", () => {
    const { container } = render(<StatusBar activeTab="notes.py" investigationId="i1" />);
    expect(container.textContent).toContain("UTF-8");
  });
});

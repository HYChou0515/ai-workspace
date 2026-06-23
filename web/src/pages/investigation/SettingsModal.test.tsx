// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { LocaleProvider } from "../../lib/i18n";
import { SettingsModal } from "./WorkspaceShell";

describe("Settings panel (#160 de-jargon + i18n)", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
  });
  afterEach(cleanup);

  function open(productName = "RCA") {
    render(
      <LocaleProvider>
        <SettingsModal
          open
          onClose={() => {}}
          theme="system"
          onTheme={() => {}}
          productName={productName}
        />
      </LocaleProvider>,
    );
  }

  it("shows the product name from the manifest, not a hardcoded version", () => {
    open("Root-Cause Analysis");
    expect(screen.getByText("Root-Cause Analysis")).toBeTruthy();
    expect(screen.queryByText(/RCA 3\.0/)).toBeNull();
  });

  it("offers a language switch that re-renders the panel in English", () => {
    open();
    expect(screen.getByText("關於")).toBeTruthy(); // zh-TW "About"
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByText("About")).toBeTruthy();
    expect(screen.queryByText("關於")).toBeNull();
  });

  it("drops engineering nouns (Swagger / contract.md) from the About rows", () => {
    open();
    expect(screen.queryByText(/Swagger/)).toBeNull();
    expect(screen.queryByText(/contract\.md/)).toBeNull();
  });
});

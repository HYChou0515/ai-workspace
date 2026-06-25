// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { FontScaleProvider } from "../hooks/fontScale";
import { LocaleProvider } from "../lib/i18n";
import { GlobalSettings } from "./GlobalSettings";

function renderSettings() {
  render(
    <LocaleProvider>
      <FontScaleProvider>
        <GlobalSettings />
      </FontScaleProvider>
    </LocaleProvider>,
  );
}

describe("GlobalSettings", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
  });
  afterEach(cleanup);

  it("opens a settings dialog from the gear button", () => {
    renderSettings();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));
    expect(screen.getByRole("dialog", { name: "設定" })).toBeInTheDocument();
  });

  it("consolidates font size, theme and language controls", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));

    // Font size (#226) — the new control.
    expect(screen.getByRole("slider", { name: "字體大小" })).toBeInTheDocument();
    // Theme — moved out of the per-App shell.
    expect(screen.getByRole("radio", { name: "系統" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "深色" })).toBeInTheDocument();
    // Language.
    expect(screen.getByRole("button", { name: "中文" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
  });

  it("closes the dialog with the close button", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

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

  // #250: the docs link was hardcoded to `/docs`, but #177 moved the backend
  // (incl. the Swagger UI) under `/api`. The link must point at `/api/docs`.
  it("points the API reference link at the backend docs under /api", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));
    expect(screen.getByRole("link", { name: "API 文件" })).toHaveAttribute("href", "/api/docs");
  });

  // #460 P4: the docs anchor read as plain text (global reset strips anchors);
  // it must carry a visible link affordance.
  it("styles the API reference with a link affordance", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));
    const link = screen.getByRole("link", { name: "API 文件" });
    expect(link.style.textDecoration).toContain("underline");
  });

  // #460 P4: "單人示範（免登入）" was wrong — the product is multi-user and does
  // resolve an identity via the auth seam; it just has no SSO yet.
  it("describes the sign-in method accurately (not single-user / no-login)", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));
    const dialog = screen.getByRole("dialog", { name: "設定" });
    expect(dialog.textContent).toContain("示範模式");
    expect(dialog.textContent).not.toContain("單人");
    expect(dialog.textContent).not.toContain("免登入");
  });
});

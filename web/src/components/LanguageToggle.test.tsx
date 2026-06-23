// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getStoredLocale, LocaleProvider } from "../lib/i18n";
import { LanguageToggle } from "./LanguageToggle";

describe("LanguageToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
  });
  afterEach(cleanup);

  function renderToggle() {
    render(
      <LocaleProvider>
        <LanguageToggle />
      </LocaleProvider>,
    );
  }

  it("marks the active locale and switches stickily on click", () => {
    renderToggle();
    expect(screen.getByRole("button", { name: "中文" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "English" }).getAttribute("aria-pressed")).toBe(
      "false",
    );

    fireEvent.click(screen.getByRole("button", { name: "English" }));

    expect(screen.getByRole("button", { name: "English" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(getStoredLocale()).toBe("en");
  });
});

// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// #250 / #73: simulate a sub-path deploy (built with VITE_BASE_PATH=/sub). The
// API reference link must carry that deploy prefix, not a bare `/api/docs`.
vi.mock("../api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/http")>()),
  API_BASE: "/sub",
  API_PREFIX: "/sub/api",
}));

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

describe("GlobalSettings root-path (#250)", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, "language", { value: "zh-TW", configurable: true });
  });
  afterEach(cleanup);

  it("prefixes the deploy base path on the API reference link", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: "設定" }));
    expect(screen.getByRole("link", { name: "API 文件" })).toHaveAttribute(
      "href",
      "/sub/api/docs",
    );
  });
});

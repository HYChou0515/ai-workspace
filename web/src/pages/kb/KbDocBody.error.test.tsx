// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { KbApi } from "../../api/kb";
import { LocaleProvider } from "../../lib/i18n";
import { QueryWrap } from "../../test/queryWrapper";
import { KbDocBody } from "./KbDocBody";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

// The real API's `ok()` throws "render document failed: <status>" (api/kb.ts).
// That raw developer string used to reach the user verbatim — e.g. opening an
// SVG whose blob 404s showed "render document failed: 404".
function failingClient(): KbApi {
  return {
    renderDocument: async () => {
      throw new Error("render document failed: 404");
    },
    getDocChunks: async () => [],
  } as unknown as KbApi;
}

describe("KbDocBody load-error copy (#465)", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("shows a friendly message instead of the raw 'failed: 404' when a doc can't load", async () => {
    localStorage.setItem("ws.locale", "en");
    render(
      <LocaleProvider>
        <KbDocBody documentId="col/u/d" onNavigate={() => {}} client={failingClient()} />
      </LocaleProvider>,
    );
    expect(
      await screen.findByText("This document couldn't be loaded. Try again in a moment."),
    ).toBeInTheDocument();
    // the raw developer error (HTTP status / internal verb) never reaches the user
    expect(screen.queryByText(/failed: 404/)).not.toBeInTheDocument();
    expect(screen.queryByText(/render document/)).not.toBeInTheDocument();
  });

  it("localizes the load-error message (zh-TW)", async () => {
    localStorage.setItem("ws.locale", "zh-TW");
    render(
      <LocaleProvider>
        <KbDocBody documentId="col/u/d" onNavigate={() => {}} client={failingClient()} />
      </LocaleProvider>,
    );
    expect(await screen.findByText("這份文件目前無法載入，請稍後再試。")).toBeInTheDocument();
  });
});

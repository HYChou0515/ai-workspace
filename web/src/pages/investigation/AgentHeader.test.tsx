// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../../test/queryWrapper";
import { AgentHeader } from "./AgentPanel";

const downloadChatExport = vi.hoisted(() => vi.fn());
vi.mock("../../api/workflows", () => ({ downloadChatExport }));

describe("AgentHeader export", () => {
  afterEach(() => {
    cleanup();
    downloadChatExport.mockReset();
  });

  it("Export downloads via the current App's route, not the removed /investigations one", () => {
    // The header is shared by every App (#89/#95). Export must target the
    // app-scoped route `/a/{slug}/items/{id}/export-chat`; the old hardcoded
    // `/investigations/...` is gone and 404s into the SPA shell (#100).
    downloadChatExport.mockResolvedValue(undefined);
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="topic-hub:1" slug="topic-hub" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(downloadChatExport).toHaveBeenCalledWith("topic-hub", "topic-hub:1");
  });

  it("surfaces an error instead of silently downloading the SPA shell", async () => {
    downloadChatExport.mockRejectedValue(new Error("匯出失敗：伺服器沒有回傳對話檔。"));
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/匯出失敗/);
  });
});

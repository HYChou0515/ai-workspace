// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "../../test/queryWrapper";
import { AgentHeader } from "./AgentPanel";

const downloadChatExport = vi.hoisted(() => vi.fn());
vi.mock("../../api/workflows", () => ({ downloadChatExport }));

vi.mock("../../api", async (orig) => {
  const actual = await orig<typeof import("../../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getItemTools: vi.fn(async () => []),
      getItemSkills: vi.fn(async () => []),
    },
  };
});

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

describe("AgentHeader new-chat escape hatch (#200)", () => {
  afterEach(cleanup);

  it("renders a New chat button and calls onNewChat when clicked", () => {
    const onNewChat = vi.fn();
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" onNewChat={onNewChat} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("omits the New chat button when onNewChat is not provided", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button", { name: /new chat/i })).not.toBeInTheDocument();
  });
});

describe("AgentHeader skills (#298)", () => {
  afterEach(cleanup);

  it("opens the Skills panel — the surface for the hidden `.skill/` folder", async () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("skills-button"));
    expect(await screen.findByTestId("skills-modal")).toBeInTheDocument();
  });
});

describe("AgentHeader tool picker (#322)", () => {
  afterEach(cleanup);

  it("renders a Tools button and opens the picker when onSaveToolPrefs is provided", async () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader
          streaming={false}
          investigationId="inv-1"
          slug="rca"
          onSaveToolPrefs={vi.fn()}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("tools-button"));
    expect(await screen.findByTestId("tools-modal")).toBeInTheDocument();
  });

  it("omits the Tools button when onSaveToolPrefs is not provided", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("tools-button")).not.toBeInTheDocument();
  });
});

describe("AgentHeader status copy (#159)", () => {
  afterEach(cleanup);

  it("when idle, shows an action cue instead of the vague 'ready'", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.getByText(/your turn/i)).toBeInTheDocument();
    expect(screen.queryByText("ready")).not.toBeInTheDocument();
  });

  it("when streaming, shows an app-neutral 'Replying…' (not RCA's 'investigating')", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={true} investigationId="inv-1" slug="topic-hub" />
      </MemoryRouter>,
    );
    expect(screen.getByText(/replying/i)).toBeInTheDocument();
    expect(screen.queryByText(/investigating/i)).not.toBeInTheDocument();
  });

  it("drops the engineering-flavoured idle badge entirely", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.queryByText("idle")).not.toBeInTheDocument();
  });

  it("drops the engineering-flavoured running badge entirely", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={true} investigationId="inv-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.queryByText("running")).not.toBeInTheDocument();
  });
});

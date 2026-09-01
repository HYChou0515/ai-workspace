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
    // The header is shared by every App (#89/#95). Export must carry the App's
    // slug so it targets the app-scoped route; the old hardcoded
    // `/investigations/...` is gone and 404s into the SPA shell (#100).
    downloadChatExport.mockResolvedValue(undefined);
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="topic-hub:1" chatId="chat-1" slug="topic-hub" />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(downloadChatExport).toHaveBeenCalledWith("topic-hub", "topic-hub:1", "chat-1");
  });

  it("exports the chat the panel is showing, not whichever one is the item's first", () => {
    // The defect this replaces: the button knew only the item, so the backend
    // resolved the item's DEFAULT chat and handed back the earliest conversation
    // whatever was on screen. An item id alone can no longer express the request.
    downloadChatExport.mockResolvedValue(undefined);
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader
          streaming={false}
          investigationId="topic-hub:1"
          slug="topic-hub"
          chatId="conversation:the-one-on-screen"
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(downloadChatExport).toHaveBeenCalledWith(
      "topic-hub",
      "topic-hub:1",
      "conversation:the-one-on-screen",
    );
  });

  it("surfaces an error instead of silently downloading the SPA shell", async () => {
    downloadChatExport.mockRejectedValue(new Error("匯出失敗：伺服器沒有回傳對話檔。"));
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" />
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
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" onNewChat={onNewChat} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("omits the New chat button when onNewChat is not provided", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" />
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
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" />
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
          chatId="chat-1"
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
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" />
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
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.getByText(/your turn/i)).toBeInTheDocument();
    expect(screen.queryByText("ready")).not.toBeInTheDocument();
  });

  it("when streaming, shows an app-neutral 'Replying…' (not RCA's 'investigating')", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={true} investigationId="inv-1" chatId="chat-1" slug="topic-hub" />
      </MemoryRouter>,
    );
    expect(screen.getByText(/replying/i)).toBeInTheDocument();
    expect(screen.queryByText(/investigating/i)).not.toBeInTheDocument();
  });

  it("drops the engineering-flavoured idle badge entirely", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.queryByText("idle")).not.toBeInTheDocument();
  });

  it("drops the engineering-flavoured running badge entirely", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={true} investigationId="inv-1" chatId="chat-1" slug="rca" />
      </MemoryRouter>,
    );
    expect(screen.queryByText("running")).not.toBeInTheDocument();
  });
});

describe("AgentHeader identity block keeps a readable width (#fe-responsive)", () => {
  afterEach(cleanup);

  // Measured in a real browser at 1440x900: the header's identity block was
  // 21px wide, so "Root Cause Analysis" rendered as "R…" and the status cue as
  // "Y…" — at EVERY viewport, because `flex: 1` (basis 0%) lets the block
  // collapse to nothing while the action buttons hold their intrinsic width.
  // The `flexWrap: "wrap"` added in #456 never engaged for the same reason:
  // a zero-basis item always "fits", so the row never has to break.
  // A non-zero basis is what makes the buttons drop to row two instead.
  it("gives the title/status block a non-zero flex basis so the buttons wrap first", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" appTitle="Root Cause Analysis" />
      </MemoryRouter>,
    );
    const block = screen.getByTestId("agent-header-identity") as HTMLElement;
    expect(block.style.flexBasis).toBe("160px");
    expect(block.style.flexGrow).toBe("1");
    expect(block.style.flexShrink).toBe("1");
    expect(block.style.minWidth).toBe("0");
  });

  it("still exposes the full App title as a tooltip once it ellipsizes", () => {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="inv-1" chatId="chat-1" slug="rca" appTitle="Root Cause Analysis" />
      </MemoryRouter>,
    );
    expect(screen.getByText("Root Cause Analysis")).toHaveAttribute("title", "Root Cause Analysis");
  });
});

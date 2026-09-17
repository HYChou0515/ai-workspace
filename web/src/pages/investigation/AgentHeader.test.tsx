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

  it("draws no Export at all on a surface that has no chat of its own", () => {
    // #739 made `chatId` optional because such surfaces exist; its context gauge
    // is simply absent there. Export follows: without a chat to name it could
    // only ask the server to pick one, and picking is the defect this replaces.
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader streaming={false} investigationId="topic-hub:1" slug="topic-hub" />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
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

describe("the header's actions step down as the column narrows", () => {
  // Wide: icon + label. Narrower: the label goes into the tooltip and the icon
  // stands alone. Narrower still: one "⋯" with the same seven behind it. Which
  // tier applies is decided by the header watching its OWN layout wrap — a
  // narrow chat column in a wide window is the common case, and a viewport
  // rule cannot see it. `tier` is the seam that pins each tier's shape here;
  // the measuring itself is asserted in a real browser.
  const ALL = [
    "new-chat-button",
    "tools-button",
    "item-environment-button",
    "env-button",
    "skills-button",
    "workflows-button",
    "export-button",
  ];
  function renderTier(tier: "labels" | "icons" | "menu", onNewChat = vi.fn()) {
    renderWithQuery(
      <MemoryRouter>
        <AgentHeader
          streaming={false}
          investigationId="topic-hub:1"
          chatId="chat-1"
          slug="topic-hub"
          onNewChat={onNewChat}
          onSaveToolPrefs={() => {}}
          environment={{ canResize: false }}
          envVars={{}}
          onSaveEnvVars={() => {}}
          tier={tier}
        />
      </MemoryRouter>,
    );
    return onNewChat;
  }
  afterEach(cleanup);

  it("wide: every action shows its label", () => {
    renderTier("labels");
    for (const id of ALL) expect(screen.getByTestId(id).textContent?.trim()).not.toBe("");
    expect(screen.queryByTestId("header-more-button")).toBeNull();
  });

  it("narrow: every action is still there, as an icon that names itself", () => {
    renderTier("icons");
    for (const id of ALL) {
      const btn = screen.getByTestId(id);
      expect(btn.textContent?.trim()).toBe("");
      expect(btn.getAttribute("aria-label")).toBeTruthy();
      expect(btn.querySelector("svg[data-icon]")?.getAttribute("width")).toBe("16");
    }
    expect(screen.queryByTestId("header-more-button")).toBeNull();
  });

  it("narrower: one more-button, and the same seven behind it, still wired", () => {
    const onNewChat = renderTier("menu");
    for (const id of ALL) expect(screen.queryByTestId(id)).toBeNull();
    fireEvent.click(screen.getByTestId("header-more-button"));
    const menu = screen.getByTestId("header-more-menu");
    expect(menu.querySelectorAll("[role='menuitem']")).toHaveLength(ALL.length);
    fireEvent.click(screen.getByTestId("header-more-new-chat"));
    expect(onNewChat).toHaveBeenCalledTimes(1);
    // picking closes it
    expect(screen.queryByTestId("header-more-menu")).toBeNull();
  });
});

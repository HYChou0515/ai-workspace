// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render as rtlRender, screen, waitFor } from "@testing-library/react";

import { QueryWrap } from "../../test/queryWrapper";

// KB views read through TanStack Query — wrap every render with a client.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: QueryWrap });
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { AskAgentDrawer } from "./AskAgentDrawer";

describe("AskAgentDrawer (fast chat)", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("renders nothing when closed", () => {
    const { container } = render(
      <AskAgentDrawer open={false} onClose={() => {}} client={mockKbApi} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a collection picker and sends a cited answer", async () => {
    await mockKbApi.createCollection("Specs");
    const onOpenCitation = vi.fn();
    render(
      <AskAgentDrawer open onClose={() => {}} onOpenCitation={onOpenCitation} client={mockKbApi} />,
    );

    // the scope picker offers the collection (default-selected)
    const chip = await screen.findByRole("button", { name: /Specs/, pressed: true });
    expect(chip).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("Ask the knowledge base…"), "why voids?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(screen.getByText(/reflow zone three drifted/i)).toBeInTheDocument(),
    );
    // tool use renders as a de-jargoned call card (#160): the friendly label,
    // never the raw `kb_search` tool name.
    expect(await screen.findByText("搜尋知識庫")).toBeInTheDocument();
    expect(screen.queryByText(/kb_search/)).not.toBeInTheDocument();

    const cite = await screen.findByRole("button", { name: /reflow\.md/i });
    await userEvent.click(cite);
    expect(onOpenCitation).toHaveBeenCalledWith(
      expect.objectContaining({ marker: 1, filename: "reflow.md" }),
    );
  });

  it("links to manage sources and chat history from the header", async () => {
    const onManage = vi.fn();
    const onHistory = vi.fn();
    render(
      <AskAgentDrawer open onClose={() => {}} onManage={onManage} onHistory={onHistory} client={mockKbApi} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /manage sources/i }));
    expect(onManage).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: /history/i }));
    expect(onHistory).toHaveBeenCalledTimes(1);
  });

  it("fires a config-driven suggestion as a question", async () => {
    render(<AskAgentDrawer open onClose={() => {}} client={mockKbApi} />);
    // suggestions come from the KB agent config, not hardcoded in the FE
    const suggestion = await screen.findByRole("button", { name: /related past findings/i });
    await userEvent.click(suggestion);
    await waitFor(() =>
      expect(screen.getByText(/reflow zone three drifted/i)).toBeInTheDocument(),
    );
  });
});

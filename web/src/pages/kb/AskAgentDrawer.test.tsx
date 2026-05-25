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
    // tool use renders RCA-style as a kb_search(…) call card
    expect(await screen.findByText(/kb_search/)).toBeInTheDocument();

    const cite = await screen.findByRole("button", { name: /reflow\.md/i });
    await userEvent.click(cite);
    expect(onOpenCitation).toHaveBeenCalledWith(
      expect.objectContaining({ marker: 1, filename: "reflow.md" }),
    );
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

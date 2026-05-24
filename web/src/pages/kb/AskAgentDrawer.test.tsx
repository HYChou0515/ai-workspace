// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { AskAgentDrawer } from "./AskAgentDrawer";

describe("AskAgentDrawer", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("renders nothing when closed", () => {
    const { container } = render(
      <AskAgentDrawer open={false} onClose={() => {}} collectionIds={[]} client={mockKbApi} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("sends a question and shows the cited answer", async () => {
    const onOpenCitation = vi.fn();
    render(
      <AskAgentDrawer
        open
        onClose={() => {}}
        collectionIds={["col-1"]}
        onOpenCitation={onOpenCitation}
        client={mockKbApi}
      />,
    );

    const box = screen.getByPlaceholderText("Ask the knowledge base…");
    await userEvent.type(box, "why voids?");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));

    // the streamed + persisted answer appears
    await waitFor(() =>
      expect(screen.getByText(/reflow zone three drifted/i)).toBeInTheDocument(),
    );
    // the citation card resolves and is clickable
    const cite = await screen.findByRole("button", { name: /reflow\.md/i });
    await userEvent.click(cite);
    expect(onOpenCitation).toHaveBeenCalledWith(
      expect.objectContaining({ marker: 1, filename: "reflow.md" }),
    );
  });

  it("fires a suggestion as a question", async () => {
    render(<AskAgentDrawer open onClose={() => {}} collectionIds={["col-1"]} client={mockKbApi} />);
    const suggestion = screen.getByRole("button", { name: /zone-3 drift/i });
    await userEvent.click(suggestion);
    await waitFor(() => expect(screen.getByText(/KB Agent/)).toBeInTheDocument());
  });
});

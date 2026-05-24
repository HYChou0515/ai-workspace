// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockKbApi, _resetKbMock } from "../../api/kbMock";
import { KbCollectionsPage } from "./KbCollectionsPage";

describe("KbCollectionsPage", () => {
  beforeEach(() => _resetKbMock());
  afterEach(cleanup);

  it("creates a collection and selects it", async () => {
    render(<KbCollectionsPage client={mockKbApi} />);
    await userEvent.type(screen.getByPlaceholderText("New collection name…"), "Process SOPs");
    await userEvent.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Process SOPs/ })).toBeInTheDocument(),
    );
    // selected → its (empty) documents pane shows the upload affordance
    expect(screen.getByRole("button", { name: /upload/i })).toBeInTheDocument();
  });

  it("uploads a document and lists it; clicking opens it", async () => {
    const onOpenDoc = vi.fn();
    const col = await mockKbApi.createCollection("kb");
    render(<KbCollectionsPage client={mockKbApi} onOpenDoc={onOpenDoc} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /kb/ })).toBeInTheDocument());

    const file = new File(["# guide"], "guide.md", { type: "text/markdown" });
    await userEvent.upload(screen.getByLabelText("Documents").querySelector("input[type=file]")!, file);

    const row = await screen.findByRole("button", { name: /guide\.md/ });
    await userEvent.click(row);
    expect(onOpenDoc).toHaveBeenCalledWith(`${col.resource_id}/me/guide.md`);
  });
});

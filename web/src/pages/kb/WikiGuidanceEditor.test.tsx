// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi } from "../../api/kb";
import { renderWithQuery } from "../../test/queryWrapper";
import { WikiGuidanceEditor } from "./WikiBrowser";

afterEach(cleanup);

function stubClient() {
  return { updateCollection: vi.fn(async () => ({})) } as unknown as KbApi;
}

function renderEditor() {
  renderWithQuery(
    <WikiGuidanceEditor
      collectionId="c1"
      maintainerGuidance=""
      readerGuidance=""
      client={stubClient()}
    />,
  );
}

describe("WikiGuidanceEditor (#460 P5)", () => {
  it("is collapsed by default so it doesn't dominate the column below the tree", () => {
    renderEditor();
    expect(screen.queryByLabelText("Writing guidance")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Answering guidance")).not.toBeInTheDocument();
  });

  it("reveals the guidance textareas when expanded", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByRole("button", { name: /wiki guidance/i }));
    expect(screen.getByLabelText("Writing guidance")).toBeInTheDocument();
    expect(screen.getByLabelText("Answering guidance")).toBeInTheDocument();
  });
});

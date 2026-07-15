// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbDocument } from "../../api/kb";
import { renderWithQuery } from "../../test/queryWrapper";
import { AttachmentBar } from "./AttachmentBar";

function att(partial: Partial<KbDocument> & { path: string }): KbDocument {
  return {
    resource_id: `id:${partial.path}`,
    content_type: "image/png",
    created_by: "me",
    status: "ready",
    parent_doc_id: "id:/d.md",
    ...partial,
  };
}

const attachments = [
  att({ path: "/d.md/.att/img.local/ring.png", content_type: "image/png", size: 2048 }),
  att({ path: "/d.md/.att/scan.pdf", content_type: "application/pdf", size: 4096 }),
];

function renderBar(over: Partial<Parameters<typeof AttachmentBar>[0]> = {}) {
  const props = {
    parentPath: "/d.md",
    attachments,
    onOpen: vi.fn(),
    onUpload: vi.fn(),
    onReplace: vi.fn(),
    onDelete: vi.fn(),
    onRename: vi.fn(),
    ...over,
  };
  renderWithQuery(<AttachmentBar {...props} />);
  return props;
}

describe("AttachmentBar", () => {
  afterEach(cleanup);

  it("lists one card per attachment with its name, type and size", () => {
    renderBar();
    const bar = screen.getByTestId("kb-attachments");
    expect(within(bar).getByText("ring.png")).toBeInTheDocument();
    expect(within(bar).getByText("scan.pdf")).toBeInTheDocument();
    // type + human size shown on the card (faithful, no per-type special-casing)
    expect(within(bar).getByText(/image\/png/)).toBeInTheDocument();
    expect(within(bar).getByText(/2 KB/)).toBeInTheDocument();
  });

  it("opens an attachment (drawer) when its card is clicked", async () => {
    const user = userEvent.setup();
    const props = renderBar();
    await user.click(screen.getByRole("button", { name: /open ring\.png/i }));
    expect(props.onOpen).toHaveBeenCalledWith("id:/d.md/.att/img.local/ring.png");
  });

  it("uploads a new attachment via the ＋ control", async () => {
    const user = userEvent.setup();
    const props = renderBar();
    const file = new File([new Uint8Array([1, 2, 3])], "new.png", { type: "image/png" });
    await user.upload(screen.getByTestId("kb-att-upload-input"), file);
    expect(props.onUpload).toHaveBeenCalledWith(file);
  });

  it("deletes an attachment from its card", async () => {
    const user = userEvent.setup();
    const props = renderBar();
    await user.click(screen.getByRole("button", { name: /delete ring\.png/i }));
    expect(props.onDelete).toHaveBeenCalledWith(attachments[0]);
  });

  it("replaces an attachment's bytes in place from its card", async () => {
    const user = userEvent.setup();
    const props = renderBar();
    const file = new File([new Uint8Array([9])], "redo.png", { type: "image/png" });
    await user.upload(screen.getByTestId("kb-att-replace-id:/d.md/.att/img.local/ring.png"), file);
    expect(props.onReplace).toHaveBeenCalledWith(attachments[0], file);
  });

  it("renames an attachment (the tail after .att/) via an inline edit", async () => {
    const user = userEvent.setup();
    const props = renderBar();
    await user.click(screen.getByRole("button", { name: /rename ring\.png/i }));
    const input = await screen.findByDisplayValue("ring.png");
    await user.clear(input);
    await user.type(input, "front.png{Enter}");
    expect(props.onRename).toHaveBeenCalledWith(attachments[0], "front.png");
  });

  it("shows only the ＋ upload control when there are no attachments", () => {
    renderBar({ attachments: [] });
    const bar = screen.getByTestId("kb-attachments");
    expect(within(bar).getByTestId("kb-att-upload-input")).toBeInTheDocument();
    expect(within(bar).queryByRole("listitem")).not.toBeInTheDocument();
  });
});

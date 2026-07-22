// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CardAttachments, docLabel } from "./CardAttachments";

afterEach(cleanup);

describe("docLabel", () => {
  it("shows the document's filename, not the opaque token", () => {
    // encode_doc_id = percent-encoded collection/user/path.
    const id = encodeURIComponent("coll-1/alice/reports/ring-defect.png");
    expect(docLabel(id)).toBe("ring-defect.png");
  });

  it("falls back to the raw token when it doesn't decode to a path", () => {
    expect(docLabel("not-a-doc-id")).toBe("not-a-doc-id");
  });
});

describe("CardAttachments", () => {
  const ids = [
    encodeURIComponent("c/u/a.png"),
    encodeURIComponent("c/u/spec.pdf"),
  ];

  it("lists every linked document by name", () => {
    render(<CardAttachments docIds={ids} editable={false} />);
    expect(screen.getByText("a.png")).toBeTruthy();
    expect(screen.getByText("spec.pdf")).toBeTruthy();
  });

  it("detaches one link without touching the others", () => {
    const onDetach = vi.fn();
    render(<CardAttachments docIds={ids} onDetach={onDetach} editable />);

    fireEvent.click(screen.getByRole("button", { name: /Detach a.png/ }));

    expect(onDetach).toHaveBeenCalledTimes(1);
    expect(onDetach).toHaveBeenCalledWith(ids[0]);
  });

  it("offers no detach buttons when not editable", () => {
    render(<CardAttachments docIds={ids} editable={false} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says so when a card has no links, only while editing", () => {
    const { rerender } = render(<CardAttachments docIds={[]} editable />);
    expect(screen.getByTestId("card-attachments-empty")).toBeTruthy();
    // Preview of an unlinked card shows nothing rather than an empty-state line.
    rerender(<CardAttachments docIds={[]} editable={false} />);
    expect(screen.queryByTestId("card-attachments-empty")).toBeNull();
  });
});

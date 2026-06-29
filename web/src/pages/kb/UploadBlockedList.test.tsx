// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UploadBlockedList } from "./UploadBlockedList";

afterEach(cleanup);

describe("UploadBlockedList (#325)", () => {
  it("lists each refused file with its reason and a count header", () => {
    render(
      <UploadBlockedList
        items={[
          { name: "deck.pptx", messageKey: "kb.upload.blocked.unreadable" },
          { name: "book.xlsx", messageKey: "kb.upload.blocked.unreadable" },
        ]}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("2 份無法接受")).toBeInTheDocument();
    expect(screen.getByText("deck.pptx")).toBeInTheDocument();
    expect(screen.getByText("book.xlsx")).toBeInTheDocument();
    // The localised "decrypt and re-upload" guidance (zh-TW default), once per file.
    expect(screen.getAllByText(/請先解密再上傳/)).toHaveLength(2);
  });

  it("calls onDismiss when the dismiss control is clicked", async () => {
    const onDismiss = vi.fn();
    render(
      <UploadBlockedList
        items={[{ name: "a.docx", messageKey: "kb.upload.blocked.unreadable" }]}
        onDismiss={onDismiss}
      />,
    );
    await userEvent.click(screen.getByText("知道了"));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("falls back to the generic reason for an unknown message key (no crash)", () => {
    render(
      <UploadBlockedList
        items={[{ name: "weird.pptx", messageKey: "totally.unknown.key" }]}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText("weird.pptx")).toBeInTheDocument();
    expect(screen.getByText(/請先解密再上傳/)).toBeInTheDocument();
  });

  it("renders nothing when there are no blocked files", () => {
    const { container } = render(<UploadBlockedList items={[]} onDismiss={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

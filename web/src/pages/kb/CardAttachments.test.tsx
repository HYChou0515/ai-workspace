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

describe("CardAttachments — attach", () => {
  const ids = [encodeURIComponent("c/u/a.png")];

  it("hands a picked file to onAttach so the card can link it", async () => {
    const onAttach = vi.fn();
    render(<CardAttachments docIds={ids} editable onAttach={onAttach} />);

    const input = screen.getByTestId("card-attach-input") as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], "ring.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(onAttach).toHaveBeenCalledTimes(1);
    expect(onAttach.mock.calls[0][0][0].name).toBe("ring.png");
  });

  it("hands a dropped file to onAttach too", () => {
    const onAttach = vi.fn();
    render(<CardAttachments docIds={ids} editable onAttach={onAttach} />);

    const zone = screen.getByTestId("card-attach-drop");
    const file = new File([new Uint8Array([9])], "dropped.png", { type: "image/png" });
    fireEvent.drop(zone, { dataTransfer: { files: [file] } });

    expect(onAttach).toHaveBeenCalledTimes(1);
    expect(onAttach.mock.calls[0][0][0].name).toBe("dropped.png");
  });

  it("shows no attach affordance when not editable", () => {
    render(<CardAttachments docIds={ids} editable={false} onAttach={vi.fn()} />);
    expect(screen.queryByTestId("card-attach-drop")).toBeNull();
  });
});

describe("CardAttachments — open", () => {
  const ids = [encodeURIComponent("c/u/a.png")];

  it("opens a linked document when its name is clicked (a link that can't be opened is useless)", () => {
    const onOpen = vi.fn();
    render(<CardAttachments docIds={ids} editable onOpen={onOpen} />);

    fireEvent.click(screen.getByRole("button", { name: /Open a.png/ }));

    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(ids[0]);
  });

  it("can open in read-only preview too — opening never mutates the card", () => {
    const onOpen = vi.fn();
    render(<CardAttachments docIds={ids} editable={false} onOpen={onOpen} />);

    fireEvent.click(screen.getByRole("button", { name: /Open a.png/ }));

    expect(onOpen).toHaveBeenCalledWith(ids[0]);
  });

  it("renders the name as plain text (not a button) when no opener is wired", () => {
    render(<CardAttachments docIds={ids} editable={false} />);
    expect(screen.getByText("a.png")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Open a.png/ })).toBeNull();
  });
});

describe("CardAttachments — image thumbnails", () => {
  const img = encodeURIComponent("c/u/diagram.png");
  const pdf = encodeURIComponent("c/u/spec.pdf");

  it("shows an image attachment as a real thumbnail, not a text pill", () => {
    render(
      <CardAttachments
        docIds={[img]}
        editable
        imageSrc={(id) => (id === img ? "/api/blobs/hash1" : undefined)}
      />,
    );
    const thumb = screen.getByRole("img", { name: /diagram.png/ });
    expect(thumb).toHaveAttribute("src", "/api/blobs/hash1");
    // and NOT the plain-text pill fallback
    expect(screen.queryByText("diagram.png")).toBeNull();
  });

  it("opens the document when the thumbnail is clicked", () => {
    const onOpen = vi.fn();
    render(
      <CardAttachments docIds={[img]} editable onOpen={onOpen} imageSrc={() => "/api/blobs/hash1"} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open diagram.png/ }));
    expect(onOpen).toHaveBeenCalledWith(img);
  });

  it("keeps a non-image attachment as a text pill", () => {
    render(<CardAttachments docIds={[pdf]} editable imageSrc={() => undefined} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("spec.pdf")).toBeTruthy();
  });

  it("still lets an image be detached", () => {
    const onDetach = vi.fn();
    render(
      <CardAttachments
        docIds={[img]}
        editable
        onDetach={onDetach}
        imageSrc={() => "/api/blobs/hash1"}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Detach diagram.png/ }));
    expect(onDetach).toHaveBeenCalledWith(img);
  });
});

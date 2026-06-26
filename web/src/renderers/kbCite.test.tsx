// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MessageCitation } from "../api/types";
import { buildByMarker, kbCiteAnchor, kbCiteUrlTransform, renderCitedText } from "./kbCite";

afterEach(cleanup);

function cite(over: Partial<MessageCitation> & { marker: number }): MessageCitation {
  return {
    collection_id: "col",
    document_id: "doc",
    filename: "spec.md",
    start: 0,
    end: 10,
    source_chunk_ids: ["ck"],
    snippet: "snip",
    ...over,
  };
}

describe("buildByMarker", () => {
  it("groups citations by their marker number", () => {
    const a = cite({ marker: 1, document_id: "d1" });
    const b = cite({ marker: 2, document_id: "d2" });
    const map = buildByMarker([a, b]);
    expect(map.get(1)).toEqual([a]);
    expect(map.get(2)).toEqual([b]);
  });

  it("collects multiple citations that share one marker, in order", () => {
    const a = cite({ marker: 1, document_id: "d1" });
    const b = cite({ marker: 1, document_id: "d2" });
    const map = buildByMarker([a, b]);
    expect(map.get(1)).toEqual([a, b]);
  });
});

describe("kbCiteUrlTransform", () => {
  it("preserves kb-cite: links the default sanitizer would strip", () => {
    expect(kbCiteUrlTransform("kb-cite:7")).toBe("kb-cite:7");
  });

  it("defers to the default transform for ordinary URLs", () => {
    expect(kbCiteUrlTransform("https://example.com")).toBe("https://example.com");
    expect(kbCiteUrlTransform("/blobs/x.png")).toBe("/blobs/x.png");
  });
});

describe("kbCiteAnchor (markdown link slot)", () => {
  it("renders a matched marker as a clickable pill that opens the first match", () => {
    const c = cite({ marker: 1, filename: "reflow.md" });
    const onOpen = vi.fn();
    const map = buildByMarker([c]);
    render(<>{kbCiteAnchor({ href: "kb-cite:1", children: "[1]" }, map, onOpen)}</>);
    const pill = screen.getByRole("button", { name: "[1]" });
    fireEvent.click(pill);
    expect(onOpen).toHaveBeenCalledWith(c);
  });

  it("lists every chunk a re-used marker maps to in the pill tooltip", () => {
    const a = cite({ marker: 1, filename: "a.md", snippet: "alpha" });
    const b = cite({ marker: 1, filename: "b.md", snippet: "bravo" });
    const map = buildByMarker([a, b]);
    render(<>{kbCiteAnchor({ href: "kb-cite:1", children: "[1]" }, map, vi.fn())}</>);
    expect(screen.getByRole("button").title).toBe("a.md — alpha\nb.md — bravo");
  });

  it("renders an unmatched marker as muted, non-clickable text keeping the literal", () => {
    const onOpen = vi.fn();
    const map = buildByMarker([cite({ marker: 1 })]);
    // marker 9 has no citation in this turn's pool
    render(<>{kbCiteAnchor({ href: "kb-cite:9", children: "[9]" }, map, onOpen)}</>);
    expect(screen.getByText("[9]")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("returns null for a non-citation href so the caller renders a normal link", () => {
    const map = buildByMarker([cite({ marker: 1 })]);
    expect(kbCiteAnchor({ href: "https://example.com", children: "x" }, map)).toBeNull();
    expect(kbCiteAnchor({ href: undefined, children: "x" }, map)).toBeNull();
  });
});

describe("renderCitedText (plain-text <pre> body)", () => {
  it("splits a matched [n] into a clickable affordance that opens the first match", () => {
    const c = cite({ marker: 1, filename: "reflow.md" });
    const onOpen = vi.fn();
    const map = buildByMarker([c]);
    const { container } = render(<pre>{renderCitedText("see [1] now", map, onOpen)}</pre>);
    fireEvent.click(screen.getByText("[1]"));
    expect(onOpen).toHaveBeenCalledWith(c);
    // surrounding prose is preserved verbatim — the body reads unchanged
    expect(container.querySelector("pre")?.textContent).toBe("see [1] now");
  });

  it("keeps the restrained pre styling — no kb-cite-inline pill in the <pre>", () => {
    const map = buildByMarker([cite({ marker: 1 })]);
    render(<pre>{renderCitedText("a [1] b", map, vi.fn())}</pre>);
    expect(document.querySelector(".kb-cite-inline")).toBeNull();
    expect(document.querySelector(".kb-cite-pre")).not.toBeNull();
  });

  it("leaves an unmatched [n] as plain, non-clickable text", () => {
    const map = buildByMarker([cite({ marker: 1 })]);
    const { container } = render(<pre>{renderCitedText("ref [9] here", map, vi.fn())}</pre>);
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toBe("ref [9] here");
  });
});

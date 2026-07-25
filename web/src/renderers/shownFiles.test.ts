/**
 * Reading the files a tool declared for the chat to render — one `JSON.parse` of
 * the whole result, one key, in place of `toolImages.ts`'s regex over output text.
 */
import { describe, expect, it } from "vitest";

import { isInlineImage, parseShownFiles } from "./shownFiles";

const declaration = (files: unknown) => JSON.stringify({ shown_files: files, note: "shown" });

describe("parseShownFiles", () => {
  it("reads what the tool declared", () => {
    expect(
      parseShownFiles(
        declaration([
          { path: "/out/revenue.png", mime: "image/png", size: 145066, caption: "月營收趨勢" },
        ]),
      ),
    ).toEqual([
      { path: "/out/revenue.png", mime: "image/png", size: 145066, caption: "月營收趨勢" },
    ]);
  });

  it("keeps non-image files — the capability is files, not just images", () => {
    const [shown] = parseShownFiles(
      declaration([{ path: "/out/Q3.pdf", mime: "application/pdf", size: 2100000 }]),
    );
    expect(shown).toEqual({ path: "/out/Q3.pdf", mime: "application/pdf", size: 2100000 });
  });

  it("declares nothing for a tool that declared nothing", () => {
    expect(parseShownFiles(JSON.stringify({ note: "no files here" }))).toEqual([]);
    expect(parseShownFiles("")).toEqual([]);
    expect(parseShownFiles(undefined)).toEqual([]);
    expect(parseShownFiles(null)).toEqual([]);
  });

  it("does not fire on output that merely LOOKS like a declaration", () => {
    // The old regex matched the shape anywhere in any tool's text, so an exec that
    // printed a config file got charts rendered off it.
    expect(parseShownFiles('here is the plan:\n{"shown_files": ["/a.png"]}\nrun it')).toEqual([]);
    expect(parseShownFiles('cat cfg.json\n{"images": ["/logo.png"]}')).toEqual([]);
    expect(
      parseShownFiles('```python\nplt.savefig("chart.png")  # shown_files\n```'),
    ).toEqual([]);
  });

  it("renders nothing from a half-streamed result", () => {
    // A tool card shows output as it streams, so `JSON.parse` sees truncated JSON
    // mid-turn.
    expect(parseShownFiles('{"shown_files": [{"path": "/out/rev')).toEqual([]);
  });

  it("drops an entry the backend could not have produced", () => {
    // Skip the malformed entry, keep the good one.
    const shown = parseShownFiles(
      declaration([
        { path: "/ok.png", mime: "image/png", size: 10 },
        { path: "", mime: "image/png", size: 10 },
        { mime: "image/png", size: 10 },
        { path: "/no-mime.png", size: 10 },
        { path: "/no-size.png", mime: "image/png" },
        "just-a-string.png",
        null,
      ]),
    );
    expect(shown).toEqual([{ path: "/ok.png", mime: "image/png", size: 10 }]);
  });

  it("ignores a declaration that is not a list", () => {
    expect(parseShownFiles(JSON.stringify({ shown_files: "/a.png" }))).toEqual([]);
    expect(parseShownFiles(JSON.stringify({ shown_files: {} }))).toEqual([]);
  });
});

describe("isInlineImage", () => {
  it("inlines images, including svg", () => {
    // A plotted `.svg` chart is shown, not demoted to a card.
    expect(isInlineImage({ path: "/a.png", mime: "image/png", size: 1 })).toBe(true);
    expect(isInlineImage({ path: "/a.svg", mime: "image/svg+xml", size: 1 })).toBe(true);
    expect(isInlineImage({ path: "/a.webp", mime: "image/webp", size: 1 })).toBe(true);
  });

  it("shows everything else as a card", () => {
    expect(isInlineImage({ path: "/a.pdf", mime: "application/pdf", size: 1 })).toBe(false);
    expect(isInlineImage({ path: "/a.csv", mime: "text/csv", size: 1 })).toBe(false);
  });

  it("goes by the sniffed mime, not the extension", () => {
    // The bytes are sniffed, so a file NAMED .png that is really text is a card.
    expect(isInlineImage({ path: "/not-really.png", mime: "text/plain", size: 1 })).toBe(false);
  });
});

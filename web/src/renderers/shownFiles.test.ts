/**
 * Reading the files a tool declared for the chat to render.
 *
 * The declaration is a trailing `[shown-files]{json}` line the backend appends —
 * `show_file` writes it, and the plotting tools' stdout is normalised into it. So
 * this splits on a fixed marker and parses, in place of `toolImages.ts`'s regex
 * over free tool text.
 */
import { describe, expect, it } from "vitest";

import { isInlineImage, parseShownFiles, stripShownFiles } from "./shownFiles";

const chart = { path: "/out/revenue.png", mime: "image/png", size: 145066 };
const declare = (files: unknown, head = "done.") =>
  `${head}\n[shown-files]${JSON.stringify({ shown_files: files })}`;

describe("parseShownFiles", () => {
  it("reads what the tool declared", () => {
    expect(parseShownFiles(declare([{ ...chart, caption: "月營收趨勢" }]))).toEqual([
      { ...chart, caption: "月營收趨勢" },
    ]);
  });

  it("keeps non-image files — the capability is files, not just images", () => {
    const pdf = { path: "/out/Q3.pdf", mime: "application/pdf", size: 2100000 };
    expect(parseShownFiles(declare([pdf]))).toEqual([pdf]);
  });

  it("reads a declaration appended to a multi-line tool result", () => {
    // The plotting-tool path: `_format_exec`'s header + the command's own stdout,
    // then the declaration.
    const out = declare([chart], 'Tool `line-chart` returned (exit_code=0):\n{"images": ["out/revenue.png"]}');
    expect(parseShownFiles(out)).toEqual([chart]);
  });

  it("declares nothing for a tool that declared nothing", () => {
    expect(parseShownFiles("Tool `exec` returned (exit_code=0):\nok")).toEqual([]);
    expect(parseShownFiles("")).toEqual([]);
    expect(parseShownFiles(undefined)).toEqual([]);
    expect(parseShownFiles(null)).toEqual([]);
  });

  it("does not fire on output that merely LOOKS like a declaration", () => {
    // The old regex matched its shape anywhere in any tool's text, so an exec that
    // printed a config file got charts rendered off it. Only the marker counts.
    expect(parseShownFiles('cat cfg.json\n{"images": ["/logo.png"]}')).toEqual([]);
    expect(parseShownFiles('{"shown_files": [{"path": "/a.png"}]}')).toEqual([]);
    expect(parseShownFiles('```python\nplt.savefig("chart.png")  # shown_files\n```')).toEqual([]);
  });

  it("renders nothing from a half-streamed declaration", () => {
    // A tool card shows output as it streams, so the JSON can arrive truncated.
    expect(parseShownFiles('done.\n[shown-files]{"shown_files": [{"path": "/out/rev')).toEqual([]);
    expect(parseShownFiles("done.\n[shown-fil")).toEqual([]);
  });

  it("takes the last declaration when a result somehow carries two", () => {
    const out = `${declare([chart])}\n[shown-files]${JSON.stringify({
      shown_files: [{ path: "/final.png", mime: "image/png", size: 1 }],
    })}`;
    expect(parseShownFiles(out)).toEqual([{ path: "/final.png", mime: "image/png", size: 1 }]);
  });

  it("drops an entry the backend could not have produced", () => {
    // Skip the malformed entry, keep the good one.
    expect(
      parseShownFiles(
        declare([
          chart,
          { path: "", mime: "image/png", size: 10 },
          { mime: "image/png", size: 10 },
          { path: "/no-mime.png", size: 10 },
          { path: "/no-size.png", mime: "image/png" },
          "just-a-string.png",
          null,
        ]),
      ),
    ).toEqual([chart]);
  });

  it("ignores a declaration that is not a list", () => {
    expect(parseShownFiles(declare("/a.png"))).toEqual([]);
    expect(parseShownFiles(declare({}))).toEqual([]);
  });
});

describe("stripShownFiles", () => {
  it("keeps the declaration out of the tool card body", () => {
    // The marker is plumbing. The card shows what the tool said, the files render
    // as themselves — neither should show the other's raw form.
    expect(stripShownFiles(declare([chart], "chart written"))).toBe("chart written");
  });

  it("leaves an undeclared result untouched", () => {
    expect(stripShownFiles("Tool `exec` returned (exit_code=0):\nok")).toBe(
      "Tool `exec` returned (exit_code=0):\nok",
    );
    expect(stripShownFiles(undefined)).toBe(undefined);
  });

  it("strips a half-streamed marker too", () => {
    // Mid-stream the marker can arrive before its JSON; showing `[shown-fil` in
    // the card would be a glitch the user sees.
    expect(stripShownFiles("chart written\n[shown-fil")).toBe("chart written");
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

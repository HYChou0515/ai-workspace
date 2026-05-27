import { describe, expect, it } from "vitest";

import { hasEditToggle, isRawEditorView, pickRenderer, rendererComponent } from "./registry";

describe("pickRenderer", () => {
  it("routes /report.vN.md to report, not generic markdown", () => {
    expect(pickRenderer("/report.v1.md")).toBe("report");
    expect(pickRenderer("/report.v42.md")).toBe("report");
    expect(pickRenderer("/brief.md")).toBe("markdown");
    expect(pickRenderer("/notes/report-template.md")).toBe("markdown");
  });

  it("routes by extension, incl. the added types (bmp, html, svg/jpeg/png, ipynb)", () => {
    expect(pickRenderer("/analyses/drift.ipynb")).toBe("notebook");
    expect(pickRenderer("/fishbone.canvas")).toBe("fishbone");
    expect(pickRenderer("/data/spc.csv")).toBe("csv");
    expect(pickRenderer("/config.json")).toBe("json");
    expect(pickRenderer("/photos/bridge.png")).toBe("image");
    expect(pickRenderer("/photos/x-ray.JPG")).toBe("image");
    expect(pickRenderer("/d.jpeg")).toBe("image");
    expect(pickRenderer("/d.svg")).toBe("image");
    expect(pickRenderer("/scan.bmp")).toBe("image");
    expect(pickRenderer("/page.html")).toBe("html");
    expect(pickRenderer("/page.htm")).toBe("html");
  });

  it("falls back to plain text for unknown extensions", () => {
    expect(pickRenderer("/data/log.txt")).toBe("text");
    expect(pickRenderer("/Makefile")).toBe("text");
  });

  it("every routed path resolves to a renderer component", () => {
    for (const p of ["/a.md", "/a.csv", "/a.html", "/a.bmp", "/a.ipynb", "/x"]) {
      expect(rendererComponent(p)).toBeTypeOf("function");
    }
  });
});

describe("isRawEditorView", () => {
  it("rawEditor types are always full-bleed", () => {
    for (const k of ["text", "json"]) {
      expect(isRawEditorView(k, false)).toBe(true);
      expect(isRawEditorView(k, true)).toBe(true);
    }
  });
  it("editToggle types are full-bleed only while editing", () => {
    for (const k of ["markdown", "image", "csv", "html"]) {
      expect(isRawEditorView(k, false)).toBe(false);
      expect(isRawEditorView(k, true)).toBe(true);
    }
  });
  it("rendered views are never full-bleed editors", () => {
    for (const k of ["notebook", "report", "fishbone"]) {
      expect(isRawEditorView(k, true)).toBe(false);
    }
  });
});

describe("hasEditToggle", () => {
  it("is true for preview⇄edit types, false for the rest", () => {
    for (const k of ["markdown", "image", "csv", "html"]) expect(hasEditToggle(k)).toBe(true);
    for (const k of ["text", "json", "notebook", "report", "fishbone"]) {
      expect(hasEditToggle(k)).toBe(false);
    }
  });
});

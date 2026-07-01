import { describe, expect, it } from "vitest";

import { hasEditToggle, isRawEditorView, pickRenderer, rendererComponent } from "./registry";

describe("pickRenderer", () => {
  it("routes /report.vN.md to report, not generic markdown", () => {
    expect(pickRenderer("/report.v1.md")).toBe("report");
    expect(pickRenderer("/report.v42.md")).toBe("report");
    expect(pickRenderer("/brief.md")).toBe("markdown");
    expect(pickRenderer("/notes/report-template.md")).toBe("markdown");
  });

  // Same anchoring as `reportVersions` in `report/versions.ts`: the by-step
  // layout the local-lab prompt recommends puts the report under a step
  // folder. F11 must still pick it up.
  it("routes report.vN.md under a step folder to the report renderer", () => {
    expect(pickRenderer("/step6-report/report.v1.md")).toBe("report");
    expect(pickRenderer("/anywhere/report.v9.md")).toBe("report");
  });

  it("does not match files where report.vN.md isn't the basename", () => {
    expect(pickRenderer("/report.v1.md.bak")).toBe("text");
    expect(pickRenderer("/notes/something-report.v1.md")).toBe("markdown");
  });

  it("routes by extension, incl. the added types (bmp, html, svg/jpeg/png, ipynb)", () => {
    expect(pickRenderer("/analyses/drift.ipynb")).toBe("notebook");
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

  // #117: a .pdf must get its own iframe preview, not fall through to the
  // catch-all text editor (which dumped the raw bytes as mojibake).
  it("routes .pdf to the pdf renderer (not the catch-all text editor)", () => {
    expect(pickRenderer("/docs/manual.pdf")).toBe("pdf");
    expect(pickRenderer("/SCAN.PDF")).toBe("pdf");
  });

  it("falls back to plain text for unknown extensions", () => {
    expect(pickRenderer("/data/log.txt")).toBe("text");
    expect(pickRenderer("/Makefile")).toBe("text");
  });

  it("#361: routes structured-data extensions to their tree/grid renderers", () => {
    expect(pickRenderer("/config.json")).toBe("json");
    expect(pickRenderer("/events.jsonl")).toBe("jsonl");
    expect(pickRenderer("/events.ndjson")).toBe("jsonl");
    expect(pickRenderer("/conf.yaml")).toBe("yaml");
    expect(pickRenderer("/conf.yml")).toBe("yaml");
    expect(pickRenderer("/data/spc.tsv")).toBe("csv");
  });

  it("every routed path resolves to a renderer component", () => {
    for (const p of ["/a.md", "/a.csv", "/a.html", "/a.bmp", "/a.ipynb", "/x"]) {
      expect(rendererComponent(p)).toBeTypeOf("function");
    }
  });
});

describe("isRawEditorView", () => {
  it("rawEditor types are always full-bleed", () => {
    for (const k of ["text"]) {
      expect(isRawEditorView(k, false)).toBe(true);
      expect(isRawEditorView(k, true)).toBe(true);
    }
  });
  it("editToggle types are full-bleed only while editing", () => {
    // #361: json / jsonl / yaml now have a tree/record preview, so they gain
    // the same preview⇄edit toggle csv has (were rawEditor before).
    for (const k of ["markdown", "image", "csv", "html", "pdf", "json", "jsonl", "yaml"]) {
      expect(isRawEditorView(k, false)).toBe(false);
      expect(isRawEditorView(k, true)).toBe(true);
    }
  });
  it("rendered views are never full-bleed editors", () => {
    for (const k of ["notebook", "report"]) {
      expect(isRawEditorView(k, true)).toBe(false);
    }
  });
});

describe("hasEditToggle", () => {
  it("is true for preview⇄edit types, false for the rest", () => {
    for (const k of ["markdown", "image", "csv", "html", "pdf", "json", "jsonl", "yaml"]) {
      expect(hasEditToggle(k)).toBe(true);
    }
    for (const k of ["text", "notebook", "report"]) {
      expect(hasEditToggle(k)).toBe(false);
    }
  });
});

import { describe, expect, it } from "vitest";

import { WUI_PROTOCOL } from "./protocol";
import {
  formatReportsForAgent,
  isWuiReportMessage,
  reportHeadline,
  type WuiReport,
} from "./report";

const pick: WuiReport = {
  id: 1,
  kind: "pick",
  message: "pointed",
  detail: {
    html: `<span id="t">42</span>`,
    marker: "chart",
    rect: { x: 12, y: 40, w: 320, h: 180 },
    styles: { display: "flex", color: "rgb(0, 0, 0)", width: "" },
  },
};

describe("isWuiReportMessage", () => {
  it("accepts our three kinds and nothing else", () => {
    for (const report of ["error", "refused", "pick"]) {
      expect(isWuiReportMessage({ proto: WUI_PROTOCOL, report, message: "x" })).toBe(true);
    }
    expect(isWuiReportMessage({ proto: WUI_PROTOCOL, report: "exec", message: "x" })).toBe(false);
  });

  it("rejects anything not carrying our protocol tag", () => {
    expect(isWuiReportMessage({ report: "error", message: "x" })).toBe(false);
    expect(isWuiReportMessage(null)).toBe(false);
  });
});

describe("reportHeadline", () => {
  it("says what happened without naming an internal", () => {
    expect(reportHeadline({ id: 1, kind: "error", message: "x is not a function", detail: null })).toContain(
      "x is not a function",
    );
    expect(reportHeadline({ id: 2, kind: "refused", message: "only its own folder", detail: null })).toMatch(
      /not allowed/i,
    );
  });

  it("names the part that was pointed at when the page labelled it", () => {
    expect(reportHeadline(pick)).toContain("chart");
  });
});

describe("formatReportsForAgent", () => {
  it("carries the computed styles, which is the closest the agent gets to looking", () => {
    const out = formatReportsForAgent("/sales", [pick]);

    expect(out).toContain("display: flex");
    expect(out).toContain("320×180");
    expect(out).toContain(`<span id="t">42</span>`);
    expect(out).toContain("chart");
  });

  it("drops empty style values rather than padding the message with noise", () => {
    expect(formatReportsForAgent("/sales", [pick])).not.toContain("width: ;");
  });

  it("leads with the user's own words when they wrote some", () => {
    const out = formatReportsForAgent("/sales", [pick], "the total is wrong");

    expect(out.indexOf("the total is wrong")).toBeLessThan(out.indexOf("I pointed at"));
  });

  it("names where the page lives, since an item can hold several", () => {
    expect(formatReportsForAgent("/sales", [])).toContain("/sales");
    expect(formatReportsForAgent("", [])).toContain("workspace root");
  });

  it("reports an error and a refusal differently, because the fix differs", () => {
    const out = formatReportsForAgent("/sales", [
      { id: 1, kind: "error", message: "boom", detail: null },
      { id: 2, kind: "refused", message: "read-only", detail: null },
    ]);

    expect(out).toContain("hit an error: boom");
    expect(out).toContain("was refused: read-only");
  });
});

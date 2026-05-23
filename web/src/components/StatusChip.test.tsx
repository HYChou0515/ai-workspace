// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SeverityChip, StatusChip, severityTone, statusTone } from "./StatusChip";

describe("severityTone", () => {
  it("returns err for P0 and P1 (halt / critical)", () => {
    expect(severityTone("P0")).toBe("err");
    expect(severityTone("P1")).toBe("err");
  });

  it("returns warn for P2 (major)", () => {
    expect(severityTone("P2")).toBe("warn");
  });

  it("returns ok for P3 and P4 (minor / cosmetic)", () => {
    expect(severityTone("P3")).toBe("ok");
    expect(severityTone("P4")).toBe("ok");
  });
});

describe("statusTone", () => {
  it("maps each status to its design tone", () => {
    expect(statusTone("triaging")).toBe("warn");
    expect(statusTone("awaiting_review")).toBe("info");
    expect(statusTone("resolved")).toBe("ok");
    expect(statusTone("abandoned")).toBe("muted");
  });
});

describe("<SeverityChip />", () => {
  it("renders the severity level as text content", () => {
    const { getByText } = render(<SeverityChip level="P1" />);
    expect(getByText("P1")).toBeTruthy();
  });

  it("annotates tone via data-tone for CSS hooks and a11y tests", () => {
    const { getByText } = render(<SeverityChip level="P0" />);
    expect(getByText("P0").getAttribute("data-tone")).toBe("err");
  });
});

describe("<StatusChip />", () => {
  it("humanizes the status label (awaiting_review → 'awaiting review')", () => {
    const { getByText } = render(<StatusChip status="awaiting_review" />);
    expect(getByText("awaiting review")).toBeTruthy();
  });

  it("annotates tone via data-tone", () => {
    const { getByText } = render(<StatusChip status="resolved" />);
    expect(getByText("resolved").getAttribute("data-tone")).toBe("ok");
  });

  it("renders a leading status dot (matches the design's RcaChip with dot)", () => {
    const { container } = render(<StatusChip status="triaging" />);
    expect(container.querySelector("[data-role='dot']")).toBeTruthy();
  });
});

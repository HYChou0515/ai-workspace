// @vitest-environment happy-dom
/**
 * HealthDot — the header indicator for AI-feature health (#51 P5).
 * Summarises the diagnostics panel into one glanceable dot that links
 * to /diagnostics. Q6: warnings never block anything — the dot only
 * informs. Copy stays jargon-free (no check ids, no model names).
 */

import { cleanup, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { HealthApi, HealthCheckRow } from "../api/health";
import { renderWithQuery } from "../test/queryWrapper";
import { HealthDot } from "./HealthDot";

function row(over: Partial<HealthCheckRow>): HealthCheckRow {
  return {
    check_id: "c",
    description: "d",
    fast: false,
    status: "pass",
    detail: "",
    latency_ms: 1,
    checked_at: 1,
    ...over,
  };
}

function client(checks: HealthCheckRow[], running = false): HealthApi {
  return {
    getChecks: async () => ({ running, checks }),
    runChecks: async () => ({ started: true }),
  };
}

function renderDot(api: HealthApi) {
  return renderWithQuery(
    <MemoryRouter>
      <HealthDot client={api} />
    </MemoryRouter>,
  );
}

describe("HealthDot", () => {
  afterEach(cleanup);

  it("links to the diagnostics page and reads 'normal' when all checks pass", async () => {
    renderDot(client([row({ check_id: "a" }), row({ check_id: "b", status: "skip" })]));
    const link = await screen.findByRole("link", { name: /working normally/i });
    expect(link.getAttribute("href")).toBe("/diagnostics");
    expect(link.getAttribute("data-health")).toBe("ok");
  });

  it("turns to a warning when any check fails — without naming internals", async () => {
    renderDot(
      client([
        row({ check_id: "a" }),
        row({ check_id: "vlm-describe", status: "fail", detail: "colour missed" }),
      ]),
    );
    const link = await screen.findByRole("link", { name: /may not be working/i });
    expect(link.getAttribute("data-health")).toBe("warn");
    // Jargon-free: the accessible copy never leaks check ids.
    expect(link.getAttribute("aria-label")).not.toMatch(/vlm|describe|check_id/i);
  });

  it("reads as an interactive control, not decoration — carries button-like chrome (#466)", async () => {
    renderDot(client([row({ check_id: "a" })]));
    const link = await screen.findByRole("link", { name: /working normally/i });
    // The `.health-dot` class supplies the hover/focus affordance that tells the
    // user this dot is a clickable link to diagnostics (not a passive status pip).
    expect(link.className).toContain("health-dot");
  });

  it("shows an unknown state before any probe has run", async () => {
    renderDot(client([row({ status: null, checked_at: null })]));
    const link = await screen.findByRole("link", { name: /status/i });
    expect(link.getAttribute("data-health")).toBe("unknown");
  });

  it("pulses while a probe round is in flight", async () => {
    renderDot(client([row({})], true));
    const link = await screen.findByRole("link", { name: /checking/i });
    expect(link.getAttribute("data-health")).toBe("running");
  });
});

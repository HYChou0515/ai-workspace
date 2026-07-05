// @vitest-environment happy-dom
/**
 * Diagnostics page (#51 P5) — the global health panel: every probe
 * with its latest outcome, a "Run all" round trigger and per-row
 * re-runs. Q2: rounds are manual + at startup, no scheduler; while one
 * is running the page says so and disables the triggers (the BE
 * refuses overlapping rounds anyway).
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HealthApi, HealthCheckRow } from "../api/health";
import { BreadcrumbProvider, useBreadcrumbTrail } from "../hooks/breadcrumbs";
import { LocaleProvider } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { DiagnosticsPage } from "./DiagnosticsPage";

function TrailProbe() {
  const trail = useBreadcrumbTrail();
  return (
    <ul data-testid="trail">
      {trail.map((c, i) => (
        <li key={i} data-to={c.to ?? ""}>
          {c.label}
        </li>
      ))}
    </ul>
  );
}

function row(over: Partial<HealthCheckRow>): HealthCheckRow {
  return {
    check_id: "embedder-default",
    description: "Default embedder produces vectors",
    fast: true,
    status: "pass",
    detail: "",
    latency_ms: 12,
    checked_at: Date.now(),
    ...over,
  };
}

function renderPage(api: HealthApi) {
  return renderWithQuery(
    <LocaleProvider>
      <MemoryRouter>
        <DiagnosticsPage client={api} />
      </MemoryRouter>
    </LocaleProvider>,
  );
}

describe("DiagnosticsPage", () => {
  // The page is localized (#465); these assertions are worded in English, so
  // run them under the English locale. The zh-TW render is covered separately.
  beforeEach(() => localStorage.setItem("ws.locale", "en"));
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("renders in Traditional Chinese under the zh-TW locale (#465)", async () => {
    localStorage.setItem("ws.locale", "zh-TW");
    renderPage({
      getChecks: async () => ({ running: false, checks: [row({})] }),
      runChecks: async () => ({ started: true }),
    });
    expect(await screen.findByRole("heading", { name: "AI 診斷" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "健康檢查" })).toBeInTheDocument();
    // the hardcoded English no longer leaks through under zh-TW
    expect(screen.queryByText("AI diagnostics")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Activity" })).not.toBeInTheDocument();
  });

  it("labels the telemetry tab 'Activity', not the OTel term 'Traces' (#171)", async () => {
    renderPage({
      getChecks: async () => ({ running: false, checks: [] }),
      runChecks: async () => ({ started: true }),
    });
    expect(await screen.findByRole("tab", { name: "Activity" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Traces" })).not.toBeInTheDocument();
  });

  it("lists every check with a readable outcome", async () => {
    const checks = [
      row({}),
      row({
        check_id: "insight-extraction",
        description: "Chat distillation model returns usable insights",
        status: "fail",
        detail: "the model answered in prose",
      }),
      row({
        check_id: "vlm-describe",
        description: "Vision model describes a known image",
        status: "skip",
        detail: "no vision model is set up",
      }),
      row({
        check_id: "agent-workspace",
        description: "Workspace agent model can call tools",
        status: null,
        checked_at: null,
        latency_ms: null,
      }),
    ];
    renderPage({
      getChecks: async () => ({ running: false, checks }),
      runChecks: async () => ({ started: true }),
    });

    expect(await screen.findByText("Default embedder produces vectors")).toBeInTheDocument();
    expect(screen.getByText("Normal")).toBeInTheDocument();
    expect(screen.getByText("Issue found")).toBeInTheDocument();
    // The failing probe's detail is surfaced — that's the actionable bit.
    expect(screen.getByText(/answered in prose/i)).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.getByText("Not checked yet")).toBeInTheDocument();
  });

  it("'Run all' triggers a full round and the panel reflects the running state", async () => {
    let running = false;
    const runChecks = vi.fn(async () => {
      running = true;
      return { started: true };
    });
    renderPage({
      getChecks: async () => ({ running, checks: [row({})] }),
      runChecks,
    });

    const btn = await screen.findByRole("button", { name: /run all checks/i });
    await userEvent.click(btn);

    expect(runChecks).toHaveBeenCalledWith();
    // The page refetches and shows the in-flight state.
    await waitFor(() => {
      expect(screen.getByText(/checking/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /run all checks/i })).toBeDisabled();
  });

  it("each row re-runs just that probe", async () => {
    const runChecks = vi.fn(async (_id?: string) => ({ started: true }));
    renderPage({
      getChecks: async () => ({ running: false, checks: [row({})] }),
      runChecks,
    });

    const btn = await screen.findByRole("button", { name: /re-run/i });
    await userEvent.click(btn);

    expect(runChecks).toHaveBeenCalledWith("embedder-default");
  });

  it("publishes a Home › Diagnostics breadcrumb", () => {
    renderWithQuery(
      <LocaleProvider>
        <MemoryRouter>
          <BreadcrumbProvider>
            <DiagnosticsPage
              client={{
                getChecks: async () => ({ running: false, checks: [] }),
                runChecks: async () => ({ started: true }),
              }}
            />
            <TrailProbe />
          </BreadcrumbProvider>
        </MemoryRouter>
      </LocaleProvider>,
    );
    const items = screen.getByTestId("trail").querySelectorAll("li");
    expect([...items].map((li) => li.textContent)).toEqual(["Home", "Diagnostics"]);
    expect(items[0].getAttribute("data-to")).toBe("/");
    expect(items[1].getAttribute("data-to")).toBe("");
  });
});

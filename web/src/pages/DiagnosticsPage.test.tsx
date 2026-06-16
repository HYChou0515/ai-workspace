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
import { afterEach, describe, expect, it, vi } from "vitest";

import type { HealthApi, HealthCheckRow } from "../api/health";
import { renderWithQuery } from "../test/queryWrapper";
import { DiagnosticsPage } from "./DiagnosticsPage";

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
    <MemoryRouter>
      <DiagnosticsPage client={api} />
    </MemoryRouter>,
  );
}

describe("DiagnosticsPage", () => {
  afterEach(cleanup);

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
});

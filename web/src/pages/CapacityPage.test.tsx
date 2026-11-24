// @vitest-environment happy-dom
/**
 * The capacity dashboard (docs/plan-capacity-usage.md §10): superusers only;
 * the headline answers how many people today's habits hold; five tabs; one
 * period at a time; an analysis too large says so. Driven by REAL responses
 * (`capacity/fixtures`, written by scripts/capacity_fixtures.py and checked
 * against the API's models by tests/capacity/test_fe_fixtures.py).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CapacityClient, CapacitySummary, Person, WhatIf } from "../capacity/api";
import { TooMuchData } from "../capacity/api";
import personFixture from "../capacity/fixtures/person.json";
import summaryFixture from "../capacity/fixtures/summary.json";
import whatifFixture from "../capacity/fixtures/whatif.json";
import { renderWithQuery } from "../test/queryWrapper";

import { CapacityPage } from "./CapacityPage";

const SUPER = { current: true };
vi.mock("../hooks/useIsSuperuser", () => ({
  useIsSuperuserState: () => ({ isSuperuser: SUPER.current, groups: [], ready: true }),
  useIsSuperuser: () => SUPER.current,
}));
vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [{ id: "user00", name: "Alice Chen", section: "", email: "" }],
  useUser: (id: string) => ({ id, name: id, section: "", email: "" }),
}));
// The drawing is not under test here (Chart.test.tsx covers it). Mocked as Chart,
// not as `../capacity/echarts`: Chart reaches that by a dynamic import, which on
// CI resolved to the real ECharts in views.test.tsx and threw on happy-dom.
vi.mock("../capacity/Chart", () => ({
  Chart: ({ label, testId }: { label: string; testId?: string }) => (
    <div role="img" aria-label={label} data-testid={testId} className="capacity-chart" />
  ),
}));

const SUMMARY = summaryFixture as unknown as CapacitySummary;

function client(over: Partial<CapacityClient> = {}): CapacityClient {
  return {
    summary: vi.fn(async () => structuredClone(SUMMARY)),
    person: vi.fn(async () => personFixture as unknown as Person),
    whatif: vi.fn(async () => whatifFixture as unknown as WhatIf),
    refresh: vi.fn(async () => {}),
    archive: vi.fn(async () => ({
      configured: false,
      free: null,
      total: null,
      warn_free_ratio: 0.2,
      block_free_ratio: 0.1,
    })),
    ...over,
  } as CapacityClient;
}

function renderPage(c: CapacityClient) {
  return renderWithQuery(
    <MemoryRouter>
      <CapacityPage client={c} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  SUPER.current = true;
});
afterEach(cleanup);

describe("CapacityPage", () => {
  it("is not for anyone but a superuser — and asks nothing of the server", async () => {
    SUPER.current = false;
    const c = client();

    renderPage(c);

    expect(await screen.findByText("只有系統管理者看得到這一頁。")).toBeInTheDocument();
    expect(c.summary).not.toHaveBeenCalled();
  });

  it("opens on the overview with the headline and every source", async () => {
    const c = client();

    renderPage(c);

    const headline = await screen.findByTestId("capacity-headline");
    expect(headline).toHaveTextContent(/人/);
    expect(c.summary).toHaveBeenCalledWith(28);
    const sources = screen.getByTestId("capacity-sources");
    expect(within(sources).getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByTestId("capacity-tab-overview")).toHaveAttribute("aria-selected", "true");
  });

  it("switches period and tab", async () => {
    const c = client();
    renderPage(c);
    await screen.findByTestId("capacity-headline");

    fireEvent.click(within(screen.getByTestId("capacity-period")).getByText("7 天"));
    await waitFor(() => expect(c.summary).toHaveBeenCalledWith(7));

    fireEvent.click(screen.getByTestId("capacity-tab-thresholds"));
    expect(await screen.findByTestId("capacity-thresholds")).toBeInTheDocument();
  });

  it("recomputes on demand", async () => {
    const c = client();
    renderPage(c);
    await screen.findByTestId("capacity-headline");

    fireEvent.click(screen.getByTestId("capacity-refresh"));

    await waitFor(() => expect(c.refresh).toHaveBeenCalledWith(28));
    await waitFor(() => expect(c.summary).toHaveBeenCalledTimes(2));
  });

  it("says plainly when a period has too much data", async () => {
    const c = client({
      summary: vi.fn(async () => {
        throw new TooMuchData(6_000_000, 5_000_000);
      }),
    });

    renderPage(c);

    const error = await screen.findByTestId("capacity-error");
    // the limit, not the count where reading stopped (always the limit + 1)
    expect(error).toHaveTextContent("5,000,000");
    expect(error).not.toHaveTextContent("6,000,000");
    expect(error).toHaveTextContent("capacity.max_rows");
  });

  it("reads the page again when the analysis behind a what-if was recomputed under it", async () => {
    // the what-if says it cannot answer: the page asks again, and its views
    // then say what they need
    const c = client({
      whatif: vi.fn(async () => ({ ...(whatifFixture as unknown as WhatIf), lines: [], ready: false })),
    });
    renderPage(c);

    fireEvent.click(await screen.findByRole("tab", { name: "試算與時程" }));

    await waitFor(() => expect(c.summary).toHaveBeenCalledTimes(2));
  });
});

describe("CapacityPage — the sandbox archive (docs/plan-archive-full.md D6)", () => {
  const GiB = 1024 ** 3;

  it("shows how much of the archive is used, how much is left, and its floors", async () => {
    const c = client({
      archive: vi.fn(async () => ({
        configured: true,
        free: 3 * GiB,
        total: 20 * GiB,
        warn_free_ratio: 0.2,
        block_free_ratio: 0.1,
      })),
    });
    renderPage(c);

    const line = await screen.findByTestId("capacity-archive");
    expect(within(line).getByText(/沙盒存檔空間|Sandbox archive/)).toBeInTheDocument();
    expect(within(line).getByText("17.0 GB / 20.0 GB")).toBeInTheDocument();
    expect(line).toHaveTextContent("15.0%");
    expect(line).toHaveTextContent("20%");
    expect(line).toHaveTextContent("10%");
    expect(within(line).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "85");
  });

  it("shows nothing for a deploy with no archive", async () => {
    const c = client();
    renderPage(c);
    await waitFor(() => expect(c.archive).toHaveBeenCalled());
    expect(screen.queryByTestId("capacity-archive")).not.toBeInTheDocument();
  });

  it("asks nothing of the server for anyone but a superuser", async () => {
    SUPER.current = false;
    const c = client();
    renderPage(c);
    await screen.findByRole("status");
    expect(c.archive).not.toHaveBeenCalled();
  });
});

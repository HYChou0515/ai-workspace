// @vitest-environment happy-dom
/**
 * The usage page's third part (docs/plan-wui-usage.md U8): every deployed
 * page's person-days and uses, for an administrator — the same table, bars,
 * side panel and raw-data link the tool and skill parts have.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WuiUsageClient, WuiUsagePage } from "../api/wuiUsage";
import { translate } from "../lib/i18n";
import { QueryWrap } from "../test/queryWrapper";
import { WuiUsagePart } from "./WuiUsagePart";

afterEach(cleanup);

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const page = (over: Partial<WuiUsagePage>): WuiUsagePage => ({
  item_id: "i1",
  page: "/report/page.ai.yaml",
  title: "Line 3",
  slug: "rca",
  person_days: 12,
  workflow: 2,
  workflow_ms: 120_000,
  tool: 5,
  tool_ms: 5_000,
  agent: 1,
  agent_ms: 30_000,
  uses: 8,
  uses_ms: 155_000,
  ...over,
});

function client(pages: WuiUsagePage[]): WuiUsageClient {
  return {
    report: vi.fn(async (days: number) => ({ days, pages })),
    detail: vi.fn(async (item_id: string, p: string, days: number) => ({
      item_id,
      page: p,
      days,
      people: [
        {
          user: "alice",
          person_days: 9,
          opens: 20,
          workflow: 2,
          workflow_ms: 120_000,
          tool: 5,
          tool_ms: 5_000,
          agent: 1,
          agent_ms: 30_000,
          uses: 8,
          uses_ms: 155_000,
        },
      ],
    })),
    rawUrl: (days: number) => `/api/admin/wui-usage.csv?days=${days}`,
  };
}

function renderPart(
  c: WuiUsageClient,
  shared = { surface: "", app: "" },
  { picked = null, pick = () => {} }: { picked?: WuiUsagePage | null; pick?: (p: WuiUsagePage | null) => void } = {},
) {
  return render(
    <MemoryRouter>
      <QueryWrap>
        <WuiUsagePart days={30} shared={shared} client={c} picked={picked} pick={pick} close={() => {}} />
      </QueryWrap>
    </MemoryRouter>,
  );
}

describe("WuiUsagePart", () => {
  it("lists each page with its person-days and each kind of use", async () => {
    renderPart(client([page({})]));

    const row = await screen.findByTestId("wui-usage-row");
    expect(row).toHaveTextContent("/report/page.ai.yaml");
    expect(row).toHaveTextContent("Line 3");
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toContain("12");
    expect(cells).toContain("2");
    expect(cells).toContain("5");
    expect(cells).toContain("8");
  });

  it("draws person-days and uses as bars", async () => {
    renderPart(client([page({})]));

    expect(await screen.findByRole("figure", { name: word("usage.wui.chart.personDays") })).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: word("usage.wui.chart.uses") })).toBeInTheDocument();
  });

  it("offers the raw data for the window as a file", async () => {
    renderPart(client([page({})]));

    const link = await screen.findByRole("link", { name: word("usage.raw") });
    expect(link).toHaveAttribute("href", "/api/admin/wui-usage.csv?days=30");
  });

  it("follows the page's App filter", async () => {
    renderPart(client([page({ slug: "rca" }), page({ item_id: "i2", slug: "pm", title: "Roadmap" })]), {
      surface: "",
      app: "pm",
    });

    const rows = await screen.findAllByTestId("wui-usage-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Roadmap");
  });

  it("says a page's uses are all the page surface when another surface is picked", async () => {
    renderPart(client([page({})]), { surface: "chat", app: "" });

    expect(await screen.findByText(word("usage.wui.notThisSurface"))).toBeInTheDocument();
    expect(screen.queryByTestId("wui-usage-row")).toBeNull();
  });

  it("says so when no page was used", async () => {
    renderPart(client([]));

    expect(await screen.findByText(word("usage.wui.empty"))).toBeInTheDocument();
  });

  it("picks a page when its name is pressed", async () => {
    const pick = vi.fn();
    renderPart(client([page({})]), undefined, { pick });
    const row = await screen.findByTestId("wui-usage-row");

    fireEvent.click(within(row).getByRole("button", { name: "/report/page.ai.yaml" }));

    expect(pick.mock.calls[0][0]).toMatchObject({ item_id: "i1", page: "/report/page.ai.yaml" });
  });

  it("asks for the page's window and shows each page's time", async () => {
    const c = client([page({})]);
    renderPart(c);

    const row = await screen.findByTestId("wui-usage-row");
    expect(c.report).toHaveBeenCalledWith(30);
    // 155 s of uses: under an hour, so minutes, rounded.
    expect(within(row).getAllByRole("cell").map((x) => x.textContent)).toContain(
      word("wui.usage.minutes", { n: 3 }),
    );
  });

  it("lets go of the picked page when its name is pressed again", async () => {
    const pick = vi.fn();
    renderPart(client([page({})]), undefined, { picked: page({}), pick });
    const row = await screen.findByTestId("wui-usage-row");

    fireEvent.click(within(row).getByRole("button", { name: "/report/page.ai.yaml" }));

    expect(pick.mock.calls[0][0]).toBeNull();
  });

  it("opens no panel for a page picked elsewhere on the usage page", async () => {
    // The usage page has one side panel; a page this part does not list is
    // another part's pick, not this part's to show.
    renderPart(client([page({})]), undefined, { picked: page({ item_id: "elsewhere" }) });

    await screen.findByTestId("wui-usage-row");
    expect(screen.queryByTestId("wui-usage-detail")).toBeNull();
  });

  it("shows the picked page's people in the side panel", async () => {
    const c = client([page({})]);
    renderPart(c, undefined, { picked: page({}) });

    const panel = await screen.findByTestId("wui-usage-detail");
    expect(await within(panel).findByText("9")).toBeInTheDocument();
    expect(within(panel).getByText("20")).toBeInTheDocument();
    expect(c.detail).toHaveBeenCalledWith("i1", "/report/page.ai.yaml", 30);
  });
});

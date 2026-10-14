// @vitest-environment happy-dom
/**
 * The administrator's tool-usage tab (docs/plan-tool-usage-stats.md P6):
 * 7 / 30 / 90 days, per tool, filterable and sortable, and one tool's
 * breakdown by person, by surface × App, by item.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { ToolUsageClient, ToolUsageDetail, ToolUsageReport } from "../api/toolUsage";
import { renderWithQuery } from "../test/queryWrapper";

import { ToolUsagePanel } from "./ToolUsagePanel";

vi.mock("../hooks/useUsers", () => ({
  useUsers: () => [],
  useUser: (id: string) => ({ id, name: id === "alice" ? "Alice Chen" : id, section: "", email: "" }),
}));

const REPORT: ToolUsageReport = {
  days: 30,
  tools: [
    { tool: "spc", source: "first_party", calls: 40, fails: 10, avg_ms: 1200 },
    { tool: "exec", source: "builtin", calls: 100, fails: 1, avg_ms: 85 },
    { tool: "acme__lookup", source: "third_party", calls: 5, fails: 5, avg_ms: 30000 },
  ],
};

const DETAIL: ToolUsageDetail = {
  tool: "spc",
  days: 30,
  by_user: [{ user: "alice", calls: 30, fails: 5, avg_ms: 1000 }],
  by_surface_app: [{ surface: "schedule", app: "rca", calls: 40, fails: 10, avg_ms: 1200 }],
  by_item: [
    { item: "rca-1", title: "Line 3", slug: "rca", calls: 39, fails: 9, avg_ms: 1100 },
    { item: "gone", title: "", slug: "", calls: 1, fails: 1, avg_ms: 50 },
  ],
};

function draw(report: ToolUsageReport = REPORT) {
  const client = {
    report: vi.fn(async () => report),
    detail: vi.fn(async () => DETAIL),
  } satisfies ToolUsageClient;
  renderWithQuery(
    <MemoryRouter>
      <ToolUsagePanel client={client} />
    </MemoryRouter>,
  );
  return client;
}

function toolOrder(): string[] {
  return screen
    .getAllByTestId("tool-usage-row")
    .map((r) => within(r).getByRole("button").textContent ?? "");
}

beforeEach(() => {
  vi.spyOn(api, "listApps").mockResolvedValue([
    { slug: "rca", title: "RCA", description: "", icon: "", color: "" },
    { slug: "pm", title: "PM", description: "", icon: "", color: "" },
  ] as never);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the report", () => {
  it("lists each tool with its source, calls, failures, failure rate and average time", async () => {
    draw();

    const row = (await screen.findAllByTestId("tool-usage-row")).find((r) =>
      within(r).queryByRole("button", { name: "spc" }),
    )!;
    expect(row).toHaveTextContent("第一方套件");
    expect(row).toHaveTextContent("40");
    expect(row).toHaveTextContent("10");
    expect(row).toHaveTextContent("25%");
    expect(row).toHaveTextContent("1.2 秒");
  });

  it("shows the busiest first", async () => {
    draw();

    await screen.findAllByTestId("tool-usage-row");
    expect(toolOrder()).toEqual(["exec", "spc", "acme__lookup"]);
  });

  it("sorts by a column the person picks, and back the other way", async () => {
    draw();
    await screen.findAllByTestId("tool-usage-row");

    fireEvent.click(screen.getByRole("button", { name: /失敗率/ }));
    expect(toolOrder()).toEqual(["acme__lookup", "spc", "exec"]);
    expect(screen.getByRole("columnheader", { name: /失敗率/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );

    fireEvent.click(screen.getByRole("button", { name: /失敗率/ }));
    expect(toolOrder()).toEqual(["exec", "spc", "acme__lookup"]);
  });

  it("asks for 30 days first, and another window when chosen", async () => {
    const client = draw();
    await screen.findAllByTestId("tool-usage-row");
    expect(client.report).toHaveBeenLastCalledWith(30, { source: "", surface: "", app: "" }, "tool");
    expect(screen.getByRole("button", { name: "30 天" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "7 天" }));

    await waitFor(() => expect(client.report).toHaveBeenLastCalledWith(7, expect.anything(), "tool"));
    expect(screen.getByRole("button", { name: "7 天" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters by source, surface and App", async () => {
    const client = draw();
    await screen.findAllByTestId("tool-usage-row");

    fireEvent.change(screen.getByLabelText("來源"), { target: { value: "third_party" } });
    fireEvent.change(screen.getByLabelText("場合"), { target: { value: "schedule" } });
    await screen.findByRole("option", { name: "PM" });
    fireEvent.change(screen.getByLabelText("App"), { target: { value: "pm" } });

    await waitFor(() =>
      expect(client.report).toHaveBeenLastCalledWith(
        30,
        { source: "third_party", surface: "schedule", app: "pm" },
        "tool",
      ),
    );
  });

  it("says when nothing was called in the window", async () => {
    draw({ days: 30, tools: [] });

    expect(await screen.findByText("這段期間沒有工具呼叫")).toBeInTheDocument();
  });
});

describe("one tool", () => {
  it("breaks it down by person, by surface and App, and by item", async () => {
    const client = draw();
    fireEvent.click(await screen.findByRole("button", { name: "spc" }));

    const detail = await screen.findByTestId("tool-usage-detail");
    expect(client.detail).toHaveBeenCalledWith("spc", 30, "tool");
    expect(await within(detail).findByText("Alice Chen")).toBeInTheDocument();
    expect(within(detail).getByText("排程 · RCA")).toBeInTheDocument();
    const link = within(detail).getByRole("link", { name: "Line 3" });
    expect(link).toHaveAttribute("href", "/a/rca/rca-1");
    // An item that no longer exists is named by its id, unlinked.
    expect(within(detail).getByText("gone")).toBeInTheDocument();
    expect(within(detail).queryByRole("link", { name: "gone" })).toBeNull();
  });
});

describe("skills (plan §8)", () => {
  const SKILLS: ToolUsageReport = {
    days: 30,
    tools: [
      { tool: "report-format", source: "profile", calls: 7, fails: 0, avg_ms: 0 },
      { tool: "author-skill", source: "shared", calls: 12, fails: 0, avg_ms: 0 },
      { tool: "my-notes", source: "workspace", calls: 2, fails: 0, avg_ms: 0 },
      { tool: "spc-review", source: "hub", calls: 3, fails: 0, avg_ms: 0 },
    ],
  };

  function drawBoth() {
    const client = {
      report: vi.fn(async (_d: number, _f?: object, kind?: string) =>
        kind === "skill" ? SKILLS : REPORT,
      ),
      detail: vi.fn(async () => DETAIL),
    } satisfies ToolUsageClient;
    renderWithQuery(
      <MemoryRouter>
        <ToolUsagePanel client={client} />
      </MemoryRouter>,
    );
    return client;
  }

  it("switches between tools and skills with a segmented control, tools first", async () => {
    const client = drawBoth();
    const kind = screen.getByRole("group", { name: "看哪一種" });
    expect(within(kind).getByRole("button", { name: "工具" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(kind).getByRole("button", { name: "Skill" }));

    await waitFor(() => expect(client.report).toHaveBeenLastCalledWith(30, expect.anything(), "skill"));
    expect(within(kind).getByRole("button", { name: "Skill" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("button", { name: "author-skill" })).toBeInTheDocument();
  });

  it("lists each skill with where it came from and how many times it was loaded — no failures, no time", async () => {
    drawBoth();
    fireEvent.click(screen.getByRole("button", { name: "Skill" }));
    await screen.findByRole("button", { name: "author-skill" });

    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Skill", "來源", "次數 ↓"]);
    expect(toolOrder()).toEqual(["author-skill", "report-format", "spc-review", "my-notes"]);
    const sources = screen
      .getAllByTestId("tool-usage-row")
      .map((r) => r.querySelectorAll("td")[1]?.textContent);
    expect(sources).toEqual(["平台內建", "App 內建", "skill hub", "自己寫的"]);
  });

  it("filters skills by their own sources", async () => {
    drawBoth();
    fireEvent.click(screen.getByRole("button", { name: "Skill" }));
    await screen.findByRole("button", { name: "author-skill" });

    const source = screen.getByRole("combobox", { name: /來源/ });
    const options = within(source)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(["全部", "平台內建", "App 內建", "skill hub", "自己寫的"]);
  });

  it("opens one skill's breakdown with a count only", async () => {
    const client = drawBoth();
    fireEvent.click(screen.getByRole("button", { name: "Skill" }));
    fireEvent.click(await screen.findByRole("button", { name: "author-skill" }));

    const detail = await screen.findByTestId("tool-usage-detail");
    expect(client.detail).toHaveBeenCalledWith("author-skill", 30, "skill");
    await within(detail).findByText("Alice Chen");
    expect(within(detail).queryByText("失敗")).toBeNull();
    expect(within(detail).queryByText("平均耗時")).toBeNull();
    // Every row is a name and a count — no cells under headers that are not there.
    for (const row of within(detail).getAllByRole("row")) {
      expect(row.children).toHaveLength(2);
    }
  });

  it("closes an open breakdown when switching kinds", async () => {
    drawBoth();
    fireEvent.click(await screen.findByRole("button", { name: "spc" }));
    await screen.findByTestId("tool-usage-detail");

    fireEvent.click(screen.getByRole("button", { name: "Skill" }));

    await waitFor(() => expect(screen.queryByTestId("tool-usage-detail")).toBeNull());
  });

  it("says when no skill was loaded in the window", async () => {
    const client = {
      report: vi.fn(async () => ({ days: 30, tools: [] })),
      detail: vi.fn(async () => DETAIL),
    } satisfies ToolUsageClient;
    renderWithQuery(
      <MemoryRouter>
        <ToolUsagePanel client={client} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Skill" }));

    expect(await screen.findByText("這段期間沒有 skill 被載入")).toBeInTheDocument();
  });
});

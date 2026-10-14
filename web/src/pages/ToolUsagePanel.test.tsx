// @vitest-environment happy-dom
/**
 * The administrator's usage tab (docs/plan-tool-usage-stats.md P6, §8.4):
 * one page, tools and skills each drawn directly — bar charts of the most
 * used, then the table — under one window and one surface / App filter; a
 * row opens its breakdown by person, by surface × App, by item.
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

const TOOLS: ToolUsageReport = {
  days: 30,
  tools: [
    { tool: "spc", source: "first_party", package: "rca-tools", calls: 40, fails: 10, avg_ms: 1200 },
    { tool: "exec", source: "builtin", package: "", calls: 100, fails: 1, avg_ms: 85 },
    {
      tool: "acme__lookup",
      source: "third_party",
      package: "acme",
      calls: 5,
      fails: 5,
      avg_ms: 30000,
    },
  ],
};

const SKILLS: ToolUsageReport = {
  days: 30,
  tools: [
    { tool: "report-format", source: "profile", calls: 7, fails: 0, avg_ms: 0 },
    { tool: "author-skill", source: "shared", calls: 12, fails: 0, avg_ms: 0 },
    { tool: "my-notes", source: "workspace", calls: 2, fails: 0, avg_ms: 0 },
    { tool: "spc-review", source: "hub", calls: 3, fails: 0, avg_ms: 0 },
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

const EMPTY: ToolUsageReport = { days: 30, tools: [] };

/** The tools report by package: acme's two commands are one row; built-ins stay. */
const BY_PACKAGE: ToolUsageReport = {
  days: 30,
  tools: [
    { tool: "exec", source: "builtin", calls: 100, fails: 1, avg_ms: 85 },
    { tool: "rca-tools", source: "first_party", package: "rca-tools", calls: 40, fails: 10, avg_ms: 1200 },
    { tool: "acme", source: "third_party", package: "acme", calls: 5, fails: 5, avg_ms: 30000 },
  ],
};

function draw(tools: ToolUsageReport = TOOLS, skills: ToolUsageReport = SKILLS) {
  const client = {
    report: vi.fn(async (_d: number, _f?: object, kind?: string, group?: string) =>
      kind === "skill" ? skills : group === "package" ? BY_PACKAGE : tools,
    ),
    detail: vi.fn(async () => DETAIL),
    rawUrl: (days: number, kind: string) => `/raw?days=${days}&kind=${kind}`,
  } satisfies ToolUsageClient;
  renderWithQuery(
    <MemoryRouter>
      <ToolUsagePanel client={client} />
    </MemoryRouter>,
  );
  return client;
}

const toolsPart = () => screen.getByRole("region", { name: "工具" });
const skillsPart = () => screen.getByRole("region", { name: "Skill" });

function order(part: HTMLElement): string[] {
  return within(part)
    .getAllByTestId("tool-usage-row")
    .map((r) => within(r).getByRole("button").textContent ?? "");
}

/** A bar chart's rows, as `name value`, top first. */
function bars(part: HTMLElement, title: string): string[] {
  const chart = within(part).getByRole("figure", { name: title });
  return within(chart)
    .getAllByRole("listitem")
    .map((li) => li.textContent?.replace(/\s+/g, " ").trim() ?? "");
}

/** A chart's value-axis labels, left to right. */
function axis(chart: HTMLElement): string[] {
  return [...chart.querySelectorAll("[data-tick]")].map((t) => t.textContent ?? "");
}

/** A chart's bar lengths, top to bottom. */
function widths(chart: HTMLElement): string[] {
  return [...chart.querySelectorAll<HTMLElement>("[data-bar]")].map((b) => b.style.width);
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

describe("one page, both kinds", () => {
  it("draws tools and skills on the page at once — no switch to find the other", async () => {
    const client = draw();

    await within(toolsPart()).findAllByTestId("tool-usage-row");
    await within(skillsPart()).findAllByTestId("tool-usage-row");
    expect(client.report).toHaveBeenCalledWith(
      30,
      { source: "", surface: "", app: "" },
      "tool",
      "command",
    );
    expect(client.report).toHaveBeenCalledWith(
      30,
      { source: "", surface: "", app: "" },
      "skill",
      "command",
    );
    expect(screen.queryByRole("group", { name: "看哪一種" })).toBeNull();
  });

  it("asks for 30 days first, and another window for both when chosen", async () => {
    const client = draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");
    expect(screen.getByRole("button", { name: "30 天" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "7 天" }));

    await waitFor(() => {
      expect(client.report).toHaveBeenCalledWith(7, expect.anything(), "tool", "command");
      expect(client.report).toHaveBeenCalledWith(7, expect.anything(), "skill", "command");
    });
    expect(screen.getByRole("button", { name: "7 天" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filters both by surface and App, and each by its own sources", async () => {
    const client = draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    fireEvent.change(screen.getByLabelText("場合"), { target: { value: "schedule" } });
    await screen.findByRole("option", { name: "PM" });
    fireEvent.change(screen.getByLabelText("App"), { target: { value: "pm" } });
    fireEvent.change(within(toolsPart()).getByLabelText("來源"), {
      target: { value: "third_party" },
    });

    await waitFor(() => {
      expect(client.report).toHaveBeenLastCalledWith(
        30,
        { source: "third_party", surface: "schedule", app: "pm" },
        "tool",
        "command",
      );
      expect(client.report).toHaveBeenCalledWith(
        30,
        { source: "", surface: "schedule", app: "pm" },
        "skill",
        "command",
      );
    });
    const skillSources = within(within(skillsPart()).getByLabelText("來源"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(skillSources).toEqual(["全部", "平台內建", "App 內建", "skill hub", "自己寫的"]);
  });
});

describe("tools", () => {
  it("charts the most called and, apart, the most failed — two bar charts, not one stacked", async () => {
    draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    expect(bars(toolsPart(), "呼叫次數")).toEqual(["exec 100", "spc 40", "acme__lookup 5"]);
    expect(bars(toolsPart(), "失敗次數")).toEqual(["spc 10", "acme__lookup 5", "exec 1"]);
  });

  it("draws each bar against a value axis from 0 to a round number", async () => {
    draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    const chart = within(toolsPart()).getByRole("figure", { name: "呼叫次數" });
    // 100, 40, 5 calls on an axis 0–50–100: lengths are value / axis end.
    expect(axis(chart)).toEqual(["0", "50", "100"]);
    expect(widths(chart)).toEqual(["100%", "40%", "5%"]);
  });

  it("draws failures on the same scale as calls, so the two charts compare", async () => {
    draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    const calls = within(toolsPart()).getByRole("figure", { name: "呼叫次數" });
    const fails = within(toolsPart()).getByRole("figure", { name: "失敗次數" });
    expect(axis(fails)).toEqual(axis(calls));
    // 10, 5, 1 failures on the calls' 0–100 axis — not stretched to their own 10.
    expect(widths(fails)).toEqual(["10%", "5%", "1%"]);
  });

  it("ends the axis on the next round tick above the largest bar", async () => {
    draw();
    await within(skillsPart()).findAllByTestId("tool-usage-row");

    const chart = within(skillsPart()).getByRole("figure", { name: "載入次數" });
    // 12 loads: ticks of 5, so the axis ends at 15 and 12 is 80% of it.
    expect(axis(chart)).toEqual(["0", "5", "10", "15"]);
    expect(widths(chart)).toEqual(["80%", "47%", "20%", "13%"]);
  });

  it("never splits a count below one on the axis", async () => {
    draw({ days: 30, tools: [{ tool: "exec", source: "builtin", calls: 1, fails: 0, avg_ms: 5 }] });
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    const chart = within(toolsPart()).getByRole("figure", { name: "呼叫次數" });
    expect(axis(chart)).toEqual(["0", "1"]);
    expect(widths(chart)).toEqual(["100%"]);
  });

  it("charts at most ten", async () => {
    const many: ToolUsageReport = {
      days: 30,
      tools: Array.from({ length: 14 }, (_, i) => ({
        tool: `t${i}`,
        source: "builtin" as const,
        calls: 100 - i,
        fails: 0,
        avg_ms: 1,
      })),
    };
    draw(many);
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    expect(bars(toolsPart(), "呼叫次數")).toHaveLength(10);
    expect(order(toolsPart())).toHaveLength(14); // the table keeps every one
  });

  it("leaves the failure chart out when nothing failed", async () => {
    draw({ days: 30, tools: [{ tool: "exec", source: "builtin", calls: 3, fails: 0, avg_ms: 5 }] });
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    expect(within(toolsPart()).queryByRole("figure", { name: "失敗次數" })).toBeNull();
  });

  it("lists each tool with its source, calls, failures, failure rate and average time", async () => {
    draw();

    const row = (await within(toolsPart()).findAllByTestId("tool-usage-row")).find((r) =>
      within(r).queryByRole("button", { name: "spc" }),
    )!;
    expect(row).toHaveTextContent("第一方套件");
    expect(row).toHaveTextContent("40");
    expect(row).toHaveTextContent("10");
    expect(row).toHaveTextContent("25%");
    expect(row).toHaveTextContent("1.2 秒");
  });

  it("sorts by a column the person picks, and back the other way", async () => {
    draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");
    expect(order(toolsPart())).toEqual(["exec", "spc", "acme__lookup"]);

    fireEvent.click(within(toolsPart()).getByRole("button", { name: /失敗率/ }));
    expect(order(toolsPart())).toEqual(["acme__lookup", "spc", "exec"]);
    expect(within(toolsPart()).getByRole("columnheader", { name: /失敗率/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );

    fireEvent.click(within(toolsPart()).getByRole("button", { name: /失敗率/ }));
    expect(order(toolsPart())).toEqual(["exec", "spc", "acme__lookup"]);
  });

  it("says when nothing was called in the window", async () => {
    draw(EMPTY);

    expect(await within(toolsPart()).findByText("這段期間沒有工具呼叫")).toBeInTheDocument();
  });

  it("breaks one tool down by person, by surface and App, and by item", async () => {
    const client = draw();
    fireEvent.click(await within(toolsPart()).findByRole("button", { name: "spc" }));

    const detail = await within(toolsPart()).findByTestId("tool-usage-detail");
    expect(client.detail).toHaveBeenCalledWith("spc", 30, "tool", "command", "first_party");
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
  it("charts the most loaded", async () => {
    draw();
    await within(skillsPart()).findAllByTestId("tool-usage-row");

    expect(bars(skillsPart(), "載入次數")).toEqual([
      "author-skill 12",
      "report-format 7",
      "spc-review 3",
      "my-notes 2",
    ]);
  });

  it("lists each skill with where it came from and how many times it was loaded — no failures, no time", async () => {
    draw();
    await within(skillsPart()).findAllByTestId("tool-usage-row");

    const heads = within(skillsPart())
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(heads).toEqual(["Skill", "來源", "次數 ↓"]);
    expect(order(skillsPart())).toEqual(["author-skill", "report-format", "spc-review", "my-notes"]);
    const sources = within(skillsPart())
      .getAllByTestId("tool-usage-row")
      .map((r) => r.querySelectorAll("td")[1]?.textContent);
    expect(sources).toEqual(["平台內建", "App 內建", "skill hub", "自己寫的"]);
  });

  it("opens one skill's breakdown, in its own part, with a count only", async () => {
    const client = draw();
    fireEvent.click(await within(skillsPart()).findByRole("button", { name: "author-skill" }));

    const detail = await within(skillsPart()).findByTestId("tool-usage-detail");
    expect(client.detail).toHaveBeenCalledWith("author-skill", 30, "skill", "command", "shared");
    await within(detail).findByText("Alice Chen");
    expect(within(detail).queryByText("失敗")).toBeNull();
    expect(within(detail).queryByText("平均耗時")).toBeNull();
    // Every row is a name and a count — no cells under headers that are not there.
    for (const row of within(detail).getAllByRole("row")) {
      expect(row.children).toHaveLength(2);
    }
    expect(within(toolsPart()).queryByTestId("tool-usage-detail")).toBeNull();
  });

  it("says when no skill was loaded in the window", async () => {
    draw(TOOLS, EMPTY);

    expect(await within(skillsPart()).findByText("這段期間沒有 skill 被載入")).toBeInTheDocument();
  });
});

describe("which tools, at which granularity (plan §9)", () => {
  /** The picker's one-line summary, which opens it. */
  const summary = () => within(toolsPart()).getByRole("button", { name: /^顯示哪些/ });
  const picker = () => within(toolsPart()).getByRole("group", { name: "顯示哪些" });

  async function openPicker() {
    await within(toolsPart()).findAllByTestId("tool-usage-row");
    fireEvent.click(summary());
    return picker();
  }

  it("is folded to one line saying what is shown — a hundred commands do not flood the page", async () => {
    draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    expect(summary()).toHaveTextContent("顯示哪些:全部(3)");
    expect(summary()).toHaveAttribute("aria-expanded", "false");
    expect(within(toolsPart()).queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("opens to tick boxes grouped by package, built-ins on their own", async () => {
    draw();
    const box = await openPicker();

    expect(summary()).toHaveAttribute("aria-expanded", "true");
    const groups = within(box)
      .getAllByRole("group")
      .map((g) => g.getAttribute("aria-label"));
    expect(groups).toEqual(["內建", "rca-tools", "acme"]);
    expect(within(within(box).getByRole("group", { name: "acme" })).getAllByRole("checkbox")).toHaveLength(1);
  });

  it("finds a command by typing part of its name", async () => {
    draw();
    const box = await openPicker();

    fireEvent.change(within(box).getByRole("searchbox", { name: "找工具" }), {
      target: { value: "look" },
    });

    expect(within(box).getAllByRole("checkbox").map((c) => c.getAttribute("name"))).toEqual([
      "acme__lookup",
    ]);
  });

  it("leaves an unticked tool out of the charts and the table, and says how many are shown", async () => {
    draw();
    const box = await openPicker();

    fireEvent.click(within(box).getByRole("checkbox", { name: "exec" }));

    expect(order(toolsPart())).toEqual(["spc", "acme__lookup"]);
    expect(bars(toolsPart(), "呼叫次數")).toEqual(["spc 40", "acme__lookup 5"]);
    expect(bars(toolsPart(), "失敗次數")).toEqual(["spc 10", "acme__lookup 5"]);
    expect(summary()).toHaveTextContent("顯示哪些:已選 2 / 3");
  });

  it("shows what is picked outside the list, each one removable", async () => {
    draw();
    const box = await openPicker();
    fireEvent.click(within(toolsPart()).getByRole("button", { name: "清除" }));
    fireEvent.click(within(box).getByRole("checkbox", { name: "spc" }));
    fireEvent.click(within(box).getByRole("checkbox", { name: "exec" }));

    const chosen = within(toolsPart()).getByRole("list", { name: "已選" });
    expect(within(chosen).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "exec✕",
      "spc✕",
    ]);

    fireEvent.click(within(chosen).getByRole("button", { name: "移除 exec" }));

    expect(order(toolsPart())).toEqual(["spc"]);
  });

  it("names the count instead of the names once more than twenty are picked", async () => {
    const many: ToolUsageReport = {
      days: 30,
      tools: Array.from({ length: 30 }, (_, i) => ({
        tool: `c${String(i).padStart(2, "0")}`,
        source: "first_party" as const,
        package: "big",
        calls: 100 - i,
        fails: 0,
        avg_ms: 1,
      })),
    };
    draw(many);
    const box = await openPicker();
    fireEvent.click(within(box).getByRole("checkbox", { name: "c00" }));

    expect(summary()).toHaveTextContent("顯示哪些:已選 29 / 30");
    expect(within(toolsPart()).queryByRole("list", { name: "已選" })).toBeNull();
  });

  it("clears every tick, then shows only the ones ticked back", async () => {
    draw();
    const box = await openPicker();

    fireEvent.click(within(toolsPart()).getByRole("button", { name: "清除" }));
    expect(within(toolsPart()).queryAllByTestId("tool-usage-row")).toHaveLength(0);
    expect(within(toolsPart()).getByText("沒有勾選要顯示的工具")).toBeInTheDocument();

    fireEvent.click(within(box).getByRole("checkbox", { name: "spc" }));
    expect(order(toolsPart())).toEqual(["spc"]);

    fireEvent.click(within(toolsPart()).getByRole("button", { name: "全部" }));
    expect(order(toolsPart())).toEqual(["exec", "spc", "acme__lookup"]);
  });

  it("adds a package's commands into one row by package, and starts from every one again", async () => {
    const client = draw();
    const box = await openPicker();
    fireEvent.click(within(box).getByRole("checkbox", { name: "exec" }));

    const unit = within(toolsPart()).getByRole("group", { name: "顯示單位" });
    expect(within(unit).getByRole("button", { name: "每個 command" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(within(unit).getByRole("button", { name: "依套件" }));

    await waitFor(() =>
      expect(client.report).toHaveBeenLastCalledWith(30, expect.anything(), "tool", "package"),
    );
    await waitFor(() => expect(order(toolsPart())).toEqual(["exec", "rca-tools", "acme"]));
    expect(summary()).toHaveTextContent("顯示哪些:全部(3)");
  });

  it("opens a package's breakdown by package", async () => {
    const client = draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");
    fireEvent.click(within(toolsPart()).getByRole("button", { name: "依套件" }));
    fireEvent.click(await within(toolsPart()).findByRole("button", { name: "acme" }));

    await within(toolsPart()).findByTestId("tool-usage-detail");
    expect(client.detail).toHaveBeenCalledWith("acme", 30, "tool", "package", "third_party");
  });

  it("has no granularity or picker for skills — a skill has no package", async () => {
    draw();
    await within(skillsPart()).findAllByTestId("tool-usage-row");

    expect(within(skillsPart()).queryByRole("group", { name: "顯示單位" })).toBeNull();
    expect(within(skillsPart()).queryByRole("button", { name: /^顯示哪些/ })).toBeNull();
  });
});

describe("raw data (plan §9.3)", () => {
  it("offers each part's raw data for the window, as a download link", async () => {
    draw();
    await within(toolsPart()).findAllByTestId("tool-usage-row");

    const tools = within(toolsPart()).getByRole("link", { name: "下載原始資料(CSV)" });
    const skills = within(skillsPart()).getByRole("link", { name: "下載原始資料(CSV)" });
    expect(tools).toHaveAttribute("href", "/raw?days=30&kind=tool");
    expect(skills).toHaveAttribute("href", "/raw?days=30&kind=skill");
    expect(tools).toHaveAttribute("download");

    fireEvent.click(screen.getByRole("button", { name: "7 天" }));

    expect(within(toolsPart()).getByRole("link", { name: "下載原始資料(CSV)" })).toHaveAttribute(
      "href",
      "/raw?days=7&kind=tool",
    );
  });
});

describe("one name from two sources (plan §8.2)", () => {
  const TWICE: ToolUsageReport = {
    days: 30,
    tools: [
      { tool: "line-report", source: "workspace", calls: 3, fails: 0, avg_ms: 0 },
      { tool: "line-report", source: "hub", calls: 2, fails: 0, avg_ms: 0 },
    ],
  };

  it("names each bar with its source, so the two can be told apart", async () => {
    draw(TOOLS, TWICE);
    await within(skillsPart()).findAllByTestId("tool-usage-row");

    expect(bars(skillsPart(), "載入次數")).toEqual([
      "line-report(自己寫的) 3",
      "line-report(skill hub) 2",
    ]);
  });

  it("opens the breakdown of the row picked — its source only", async () => {
    const client = draw(TOOLS, TWICE);
    const rows = await within(skillsPart()).findAllByTestId("tool-usage-row");

    fireEvent.click(within(rows[1]!).getByRole("button", { name: "line-report" }));

    await within(skillsPart()).findByTestId("tool-usage-detail");
    expect(client.detail).toHaveBeenCalledWith("line-report", 30, "skill", "command", "hub");
    expect(rows[1]).toHaveAttribute("aria-selected", "true");
    expect(rows[0]).toHaveAttribute("aria-selected", "false");
  });
});

// @vitest-environment happy-dom
/**
 * P8 — the panel that makes "refuse outright" a survivable policy.
 *
 * The acceptance loop from the plan: refused → open the panel → close → the
 * same thing works. The close half is exercised here against a client double;
 * the backend half is `tests/quota/test_routes.py`.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MyResources, MyResourcesApi } from "../api/myResources";
import { MemoryRouter } from "react-router-dom";

import { QueryWrap } from "../test/queryWrapper";
import { MyResourcesPage, formatAgainstLimit, formatBytes } from "./MyResourcesPage";

function data(over: Partial<MyResources> = {}): MyResources {
  return {
    owner: "alice",
    limits: { count: 2, cpu: 0, memory_bytes: 0, disk_bytes: 1024 },
    live: [
      {
        item_id: "i-1",
        slug: "rca",
        title: "Line 3 stoppage",
        cpu_cores: 2,
        memory_bytes: 512,
      },
    ],
    workspaces: [{ item_id: "i-1", slug: "rca", title: "Line 3 stoppage", bytes_used: 800 }],
    cpu_in_use: 2,
    memory_in_use: 512,
    disk_in_use: 800,
    ...over,
  };
}

function client(over: Partial<MyResourcesApi> = {}): MyResourcesApi {
  return {
    get: vi.fn(async () => data()),
    closeEnvironment: vi.fn(async () => {}),
    ...over,
  };
}

afterEach(cleanup);

/** The page links to the items it lists, so it needs a router as well as a
 *  query client — the links ARE the affordance (you close from here, but you go
 *  to the item to delete files). */
function Wrap({ children }: { children: React.ReactNode }) {
  return (
    <MemoryRouter>
      <QueryWrap>{children}</QueryWrap>
    </MemoryRouter>
  );
}

describe("MyResourcesPage", () => {
  it("names the environments it lists, not just their count", async () => {
    // A list of things to close is useless if you cannot tell which is which.
    // The same item shows in both halves (it is holding an environment AND
    // storing bytes), so both lists must name it.
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    expect(await screen.findAllByText("Line 3 stoppage")).toHaveLength(2);
  });

  it("shows usage against the limit so a refusal makes sense", async () => {
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    expect(await screen.findByText(/1 個 \/ 2 個/)).toBeTruthy();
    expect(screen.getByText(/800 B \/ 1.0 KB/)).toBeTruthy();
  });

  it("closes an environment and refetches, so the freed slot is visible", async () => {
    const closeEnvironment = vi.fn(async () => {});
    const get = vi
      .fn<() => Promise<MyResources>>()
      .mockResolvedValueOnce(data())
      .mockResolvedValue(data({ live: [], cpu_in_use: 0, memory_in_use: 0 }));

    render(<MyResourcesPage client={client({ get, closeEnvironment })} />, {
      wrapper: Wrap,
    });
    await userEvent.click(await screen.findByRole("button", { name: "關閉" }));

    expect(closeEnvironment).toHaveBeenCalledWith("i-1");
    // the panel re-reads, so the person can see the slot came back
    await waitFor(() => expect(screen.getByText(/目前沒有執行中的環境/)).toBeTruthy());
  });

  it("says how to free disk, because deleting happens in the item", async () => {
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    expect(await screen.findByText(/刪除永遠不受額度限制/)).toBeTruthy();
  });

  it("renders an empty state rather than a bare zero", async () => {
    const empty = data({ live: [], workspaces: [], cpu_in_use: 0, disk_in_use: 0 });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => empty) })} />, {
      wrapper: Wrap,
    });
    expect(await screen.findByText(/目前沒有執行中的環境/)).toBeTruthy();
    expect(screen.getByText(/還沒有任何項目佔用空間/)).toBeTruthy();
  });
});

describe("formatting", () => {
  it("renders bytes at a readable scale", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(900)).toBe("900 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(20 * 1024 ** 3)).toBe("20 GB");
  });

  it("omits the denominator when a dimension is unlimited", () => {
    // 0 means "no limit" throughout this feature; printing "of 0" would read as
    // a limit of nothing, which is the opposite of what it means.
    expect(formatAgainstLimit(5, 0, (n) => `${n}`)).toBe("5");
    expect(formatAgainstLimit(5, 10, (n) => `${n}`)).toBe("5 / 10");
  });
});

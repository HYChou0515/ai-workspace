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

import type { MyResources, MyResourcesApi, UserQuotaOverride } from "../api/myResources";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({ api: { getMe: vi.fn(async () => ({ id: "alice", is_superuser: false, groups: [] })) } }));

import { api } from "../api";
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
    disk_tracked: true,
    ...over,
  };
}

/** A double that models the real endpoints' CONTRACT, not just their shape:
 * `adminGet` answers null the way the backend's 404 does (for an unknown user
 * AND for a caller who isn't a superuser — it does not distinguish, so that
 * "who has an exception" can't be probed), and `adminSet` REPLACES the whole
 * override, which is what makes pre-filling the form from effective limits a
 * bug rather than a convenience. */
function client(over: Partial<MyResourcesApi> = {}): MyResourcesApi {
  const overrides = new Map<string, UserQuotaOverride>();
  return {
    get: vi.fn(async () => data()),
    closeEnvironment: vi.fn(async () => {}),
    adminGet: vi.fn(async (userId: string) => {
      if (userId !== "bob") return null;
      const o = overrides.get(userId);
      return data({
        owner: userId,
        limits: {
          count: o?.count || 2,
          cpu: o?.cpu || 0,
          memory_bytes: 0,
          disk_bytes: 1024,
        },
      });
    }),
    adminSet: vi.fn(async (userId: string, limits: UserQuotaOverride) => {
      overrides.set(userId, limits); // replace, never merge
    }),
    adminClear: vi.fn(async (userId: string) => {
      overrides.delete(userId);
    }),
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

describe("per-person overrides (superuser)", () => {
  const asUser = (isSuperuser: boolean) =>
    vi.mocked(api.getMe).mockResolvedValue({ id: "root", is_superuser: isSuperuser, groups: [] });

  it("is invisible to a normal user — the backend would 404 it anyway", async () => {
    asUser(false);
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    await screen.findAllByText("Line 3 stoppage");
    expect(screen.queryByText(/個人額度/)).not.toBeInTheDocument();
  });

  it("lets a superuser raise one person, and says it needs no restart", async () => {
    asUser(true);
    const c = client();
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });

    await userEvent.type(await screen.findByLabelText("使用者 id"), "bob");
    await userEvent.type(screen.getByLabelText("同時執行環境上限"), "5");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));

    expect(c.adminSet).toHaveBeenCalledWith("bob", { count: 5, cpu: 0, memory: "", disk: "" });
    expect(await screen.findByText(/不需重啟/)).toBeInTheDocument();
  });

  // The read endpoint returns EFFECTIVE limits and `PUT` replaces all four
  // dimensions. Pre-filling the form from what was read would submit inherited
  // values back as explicit overrides — pinning this person to today's defaults
  // for ever. Blank must stay blank.
  it("does not turn the person's inherited limits into pinned overrides", async () => {
    asUser(true);
    const c = client();
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });

    await userEvent.type(await screen.findByLabelText("使用者 id"), "bob");
    await userEvent.click(screen.getByRole("button", { name: "查詢" }));
    // bob's effective disk is 1024 B, read back and shown...
    await screen.findByText(/目前有效額度/);
    // ...but the disk field stays empty, so saving only `count` leaves disk on
    // the deploy default rather than freezing it at 1024.
    expect(screen.getByLabelText("儲存空間上限")).toHaveValue("");

    await userEvent.type(screen.getByLabelText("同時執行環境上限"), "9");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(c.adminSet).toHaveBeenCalledWith("bob", { count: 9, cpu: 0, memory: "", disk: "" });
  });

  it("says so when the id matches nobody, rather than showing an empty form", async () => {
    asUser(true);
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });

    await userEvent.type(await screen.findByLabelText("使用者 id"), "nobody");
    await userEvent.click(screen.getByRole("button", { name: "查詢" }));
    expect(await screen.findByText(/查不到這個使用者/)).toBeInTheDocument();
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

// @vitest-environment happy-dom
/**
 * P8 — the panel that makes "refuse outright" a survivable policy.
 *
 * The acceptance loop from the plan: refused → open the panel → close → the
 * same thing works. The close half is exercised here against a client double;
 * the backend half is `tests/quota/test_routes.py`.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MyResources, MyResourcesApi, UserQuotaOverride } from "../api/myResources";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({
  api: {
    getMe: vi.fn(async () => ({ id: "alice", is_superuser: false, groups: [] })),
    // The company directory the person-picker reads. Two people, so a test can
    // show that picking one targets THAT id rather than whatever was typed.
    getUsers: vi.fn(async () => [
      { id: "bob", name: "Bob Chen", section: "Reflow", email: "bob@example.com" },
      { id: "carol", name: "Carol Wu", section: "Etch", email: "carol@example.com" },
    ]),
  },
}));

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
    adminList: vi.fn(async () => ({
      defaults: { count: 2, cpu: 0, memory_bytes: 0, disk_bytes: 1024 },
      // only the dimensions actually granted — the route returns the RAW
      // override, so an ungranted dimension is 0/"" and not the default
      overrides: [...overrides.entries()].map(([user_id, o]) => ({
        user_id,
        count: o.count ?? 0,
        cpu: o.cpu ?? 0,
        memory: o.memory ?? "",
        disk: o.disk ?? "",
      })),
    })),
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

  // Any of the three can refuse a turn on its own, so each needs its own
  // reading. They used to share one line and ONE bar — and that bar tracked
  // `count`, so it could sit at 50% while cpu was at 25% and mean neither.
  it("gives cpu and memory their own reading, not a share of the count's", async () => {
    const d = data({ limits: { count: 2, cpu: 4, memory_bytes: 2048, disk_bytes: 1024 } });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    expect(await screen.findByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("2 / 4")).toBeInTheDocument();
    expect(screen.getByText("512 B / 2.0 KB")).toBeInTheDocument();
    // one bar per dimension: 3 live + 1 storage
    expect(document.querySelectorAll(".meter")).toHaveLength(4);
  });

  // Hiding an unlimited dimension hid its USAGE too, so on a deploy that caps
  // only the environment count you could not find out how much cpu you held.
  it("still reports usage for a dimension with no limit", async () => {
    const d = data({ limits: { count: 2, cpu: 0, memory_bytes: 0, disk_bytes: 1024 } });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    expect(await screen.findByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // used, with no denominator
    expect(screen.getAllByText("（無上限）").length).toBeGreaterThan(0);
    // no bar where there is nothing to be a fraction of: count + storage only
    expect(document.querySelectorAll(".meter")).toHaveLength(2);
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

  // An exception is granted to a PERSON, and the directory is what knows who
  // exists. Typing a raw id meant a typo silently created an allowance for
  // nobody — it saves fine, shows up in the list, and never binds to a user.
  // The field lost its name when the input became a picker: a `<label>` outside
  // the component cannot reach the search box inside it, so `htmlFor` pointed at
  // nothing and the only cue left was a placeholder. Removing the fix left every
  // test green, because they all click a person's name.
  it("names the person field, rather than leaving a label pointing at nothing", async () => {
    asUser(true);
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    // by ROLE: `findByLabelText` also matches a name parked on a wrapper, so it
    // passed with the picker itself still nameless.
    expect(await screen.findByRole("searchbox", { name: "對象" })).toBeInTheDocument();
  });

  it("grants the exception to whoever was picked from the directory", async () => {
    asUser(true);
    const c = client();
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });

    await userEvent.click(await screen.findByRole("button", { name: /Bob Chen/ }));
    await userEvent.type(screen.getByLabelText("同時執行環境上限"), "5");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));

    expect(c.adminSet).toHaveBeenCalledWith("bob", { count: 5, cpu: 0, memory: "", disk: "" });
  });

  it("lets a superuser raise one person, and says it needs no restart", async () => {
    asUser(true);
    const c = client();
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });

    await userEvent.click(await screen.findByRole("button", { name: /Bob Chen/ }));
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

    await userEvent.click(await screen.findByRole("button", { name: /Bob Chen/ }));
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

  // Without a list, an operator can only confirm an exception they already
  // suspect — "who has one?" had no answer at all.
  it("names who is above the baseline, and what the baseline is", async () => {
    asUser(true);
    const c = client();
    await c.adminSet("bob", { count: 9, cpu: 0, memory: "", disk: "" });
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });

    expect(await screen.findByText(/^站台預設:/)).toHaveTextContent("同時執行環境上限 2");
    expect(screen.getByText("bob")).toBeInTheDocument();
    // only the dimension actually granted — not every dimension merged against
    // the default, which would make everyone look overridden everywhere
    expect(screen.getByText(/同時執行環境上限 9$/)).toBeInTheDocument();
  });

  it("says plainly when nobody has an exception", async () => {
    asUser(true);
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    expect(await screen.findByText(/目前沒有人有特例/)).toBeInTheDocument();
  });

  it("revokes an exception straight from the list", async () => {
    asUser(true);
    const c = client();
    await c.adminSet("bob", { count: 9, cpu: 0, memory: "", disk: "" });
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });

    const row = (await screen.findByText("bob")).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "清除覆寫" }));

    expect(c.adminClear).toHaveBeenCalledWith("bob");
    await waitFor(() => expect(screen.getByText(/目前沒有人有特例/)).toBeInTheDocument());
  });

  // Picking from the directory does not make this case go away: the directory
  // and the backend are different sources, so a listed person the backend has
  // never seen still has to read as "no such user" rather than a blank form
  // that looks ready to save.
  it("says so when the person matches nobody the backend knows", async () => {
    asUser(true);
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });

    await userEvent.click(await screen.findByRole("button", { name: /Carol Wu/ }));
    await userEvent.click(screen.getByRole("button", { name: "查詢" }));
    expect(await screen.findByText(/查不到這個使用者/)).toBeInTheDocument();
  });
});

describe("formatting", () => {
  it("renders bytes at a readable scale", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(900)).toBe("900 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    // One decimal above 1 KB — `lib/bytes`'s shipped convention, shared with
    // the usage bar. A second copy of this function briefly rendered the same
    // number as "20 GB", so a refusal message and the bar beside it disagreed.
    expect(formatBytes(20 * 1024 ** 3)).toBe("20.0 GB");
  });

  it("omits the denominator when a dimension is unlimited", () => {
    // 0 means "no limit" throughout this feature; printing "of 0" would read as
    // a limit of nothing, which is the opposite of what it means.
    expect(formatAgainstLimit(5, 0, (n) => `${n}`)).toBe("5");
    expect(formatAgainstLimit(5, 10, (n) => `${n}`)).toBe("5 / 10");
  });
});

describe("a close that could not be confirmed", () => {
  afterEach(cleanup);

  it("says so, and leaves the row there to press again", async () => {
    // The backend can now answer "I found a sandbox, asked it to go, and could
    // not confirm that it did". Rendering nothing for that is what made this
    // button unreliable: the person is told it worked while it did not, and
    // there is nothing on the page to act on either way.
    const closeEnvironment = vi.fn(async () => {
      throw new Error("close environment failed: 409");
    });
    render(<MyResourcesPage client={client({ closeEnvironment })} />, {
      wrapper: Wrap,
    });
    await userEvent.click(await screen.findByRole("button", { name: "關閉" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/無法確認/);
    // still listed, so "try again" points at something that is there
    expect(screen.getByRole("button", { name: "關閉" })).toBeTruthy();
  });
});

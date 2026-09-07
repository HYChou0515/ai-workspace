// @vitest-environment happy-dom
/**
 * P8 — the panel that makes "refuse outright" a survivable policy.
 *
 * The acceptance loop from the plan: refused → open the panel → close → the
 * same thing works. The close half is exercised here against a client double;
 * the backend half is `tests/quota/test_routes.py`.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MyResources, MyResourcesApi, UserQuotaOverride } from "../api/myResources";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({
  api: {
    getMe: vi.fn(async () => ({ id: "alice", is_superuser: false, groups: [] })),
    deleteAppItem: vi.fn(async () => {}),
    // The company directory the person-picker reads. Two people, so a test can
    // show that picking one targets THAT id rather than whatever was typed.
    getUsers: vi.fn(async () => [
      { id: "bob", name: "Bob Chen", section: "Reflow", email: "bob@example.com" },
      { id: "carol", name: "Carol Wu", section: "Etch", email: "carol@example.com" },
    ]),
    // The App manifests the rows name themselves by. Two Apps, so a test can
    // show a row picks ITS OWN App rather than whichever came back first.
    //
    // `color` is a raw hex and `icon` a named-icon key because that is what the
    // shipped `app.json` files actually carry (`#F0502E` / `flame`). An earlier
    // version of this double used `"cat-1"`, a chip-token name — a format the
    // backend never sends, which would have let colour handling be written and
    // tested against a shape that does not exist.
    listApps: vi.fn(async () => [
      { slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" },
      { slug: "pm", title: "專案管理", description: "", icon: "kanban", color: "#3B82F6" },
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

  it("says what closing actually gives back, and what it does not", async () => {
    // Closing returns cpu and memory at once and returns NO bytes — the two
    // halves of this page are refunded by different actions. The page never
    // said so, so somebody at their STORAGE limit could press Close on every
    // row, watch that gauge not move, and conclude the button was broken.
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    const live = await screen.findByRole("region", { name: "執行環境" });
    expect(live).toHaveTextContent(/CPU 與記憶體/);
    expect(live).toHaveTextContent(/檔案會保留/);
    // …and what it does NOT keep. The first version of this sentence said the
    // workspace "reopens as you left it", which is false: the mirror does not
    // persist `node_modules/`, `.venv/` or `.git/`, so an install is gone after
    // a recycle. The reader this line exists for is the one hesitating over
    // Close having just run one, so the omission pointed the wrong way.
    expect(live).toHaveTextContent(/套件與版本紀錄不會/);
  });

  it("names the App each environment belongs to, not its slug", async () => {
    // Titles alone do not say what an environment IS. The payload has carried
    // the slug all along (the row links with it) and the row spent it without
    // showing it, so a list spanning three Apps looked like one flat list and
    // the reader had to guess from the wording of a title.
    //
    // The App's own title, from its manifest — never a slug hardcoded in the
    // FE, and never the slug shown raw when a real name is available.
    const d = data({
      live: [
        { item_id: "i-1", slug: "rca", title: "Line 3 stoppage", cpu_cores: 1, memory_bytes: 800 },
        { item_id: "i-2", slug: "pm", title: "Q4 roadmap", cpu_cores: 1, memory_bytes: 800 },
      ],
      workspaces: [],
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const live = await screen.findByRole("region", { name: "執行環境" });
    const rowOf = (title: string) => within(live).getByText(title).closest("li")!;
    await waitFor(() => expect(within(rowOf("Line 3 stoppage")).getByText("根因分析")).toBeTruthy());
    expect(within(rowOf("Q4 roadmap")).getByText("專案管理")).toBeTruthy();
  });

  it("names the App on the STORAGE rows too, not just the live ones", async () => {
    // The two lists answer the same question about the same items — which of my
    // things is this? — so naming the App on one and not the other left the
    // reader able to tell a pm workspace from an rca one while deciding what to
    // close, and unable to while deciding what to DELETE, which is the
    // irreversible half.
    const d = data({
      live: [],
      workspaces: [
        { item_id: "i-1", slug: "rca", title: "Line 3 stoppage", bytes_used: 900 },
        { item_id: "i-2", slug: "pm", title: "Q4 roadmap", bytes_used: 100 },
      ],
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const disk = await screen.findByRole("region", { name: "儲存空間" });
    const rowOf = (title: string) => within(disk).getByText(title).closest("li")!;
    await waitFor(() => expect(within(rowOf("Line 3 stoppage")).getByText("根因分析")).toBeTruthy());
    expect(within(rowOf("Q4 roadmap")).getByText("專案管理")).toBeTruthy();
  });

  it("tints each pill with its OWN App's declared colour", async () => {
    // "The pill's colour is the App's colour" — so it has to come from the
    // manifest hex, per row, not from one shared accent. Asserted on both rows
    // because a single-row test passes just as well when every pill is painted
    // with whichever App happened to load first.
    const d = data({
      live: [
        { item_id: "i-1", slug: "rca", title: "Line 3 stoppage", cpu_cores: 1, memory_bytes: 800 },
        { item_id: "i-2", slug: "pm", title: "Q4 roadmap", cpu_cores: 1, memory_bytes: 800 },
      ],
      workspaces: [],
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const live = await screen.findByRole("region", { name: "執行環境" });
    const tagIn = (title: string) =>
      within(live).getByText(title).closest("li")!.querySelector(".app-tag") as HTMLElement;

    await waitFor(() => expect(tagIn("Line 3 stoppage")).not.toBeNull());
    // #F0502E and #3B82F6 — the two `app.json` colours the double declares.
    expect(tagIn("Line 3 stoppage").style.getPropertyValue("--app-tint")).toMatch(/240,\s*80,\s*46/);
    expect(tagIn("Q4 roadmap").style.getPropertyValue("--app-tint")).toMatch(/59,\s*130,\s*246/);
  });

  it("falls back to the slug for an App the manifest list does not name", async () => {
    // `listApps` is a separate near-static query: it is EMPTY on first paint and
    // stays empty if that request fails. Rendering nothing for the App would
    // make the label flicker in and, on a failure, silently leave every row
    // looking like it belongs to nothing at all.
    vi.mocked(api.listApps).mockResolvedValueOnce([]);
    const d = data({
      live: [
        { item_id: "i-1", slug: "rca", title: "Line 3 stoppage", cpu_cores: 1, memory_bytes: 800 },
      ],
      workspaces: [],
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const live = await screen.findByRole("region", { name: "執行環境" });
    const row = within(live).getByText("Line 3 stoppage").closest("li")!;
    expect(within(row).getByText("rca")).toBeTruthy();
  });

  it("shows no App tag at all for a row the backend could not name", async () => {
    // `/me/resources` degrades a row it cannot resolve to empty strings rather
    // than dropping it — the environment is running and being charged for, so
    // it has to stay closable even when its item is gone or the reader may not
    // see its title. An empty slug must therefore render NO tag: a chip has
    // padding and a fill, so an empty one is a visible grey smudge in the
    // column where every other row says something.
    const d = data({
      live: [{ item_id: "i-9", slug: "", title: "", cpu_cores: 1, memory_bytes: 800 }],
      workspaces: [],
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const live = await screen.findByRole("region", { name: "執行環境" });
    const row = within(live).getByText("i-9").closest("li")!;
    expect(row.querySelector(".app-tag")).toBeNull();
    // …and it is still closable, which is the whole reason the row is here.
    expect(within(row).getByRole("button", { name: "關閉" })).toBeEnabled();
    // The title must NOT be a link. With an empty slug it built `/a//i-9`,
    // which matches no route, so the catch-all `*` navigates to `/` with
    // `replace` — one click on the title ejects the reader from the page and
    // Back does not bring them back. The row exists to be closed; its only
    // other affordance must not throw the reader off the one page that offers
    // the remedy.
    expect(within(row).queryByRole("link")).toBeNull();
  });

  it("does not link a storage row the backend could not name either", async () => {
    // The delete button is already gated on the slug ("a GHOST row has nothing
    // this button could delete"), which left the ungated link as the row's ONLY
    // clickable thing — and it navigates nowhere.
    const d = data({
      live: [],
      workspaces: [{ item_id: "i-9", slug: "", title: "", bytes_used: 400 }],
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const disk = await screen.findByRole("region", { name: "儲存空間" });
    const row = within(disk).getByText("i-9").closest("li")!;
    expect(within(row).queryByRole("link")).toBeNull();
    expect(within(row).queryByRole("button")).toBeNull();
  });

  it("keeps the three totals out of the list of things you can close", async () => {
    // They are two different KINDS of line — "how full am I" and "here is one
    // thing you could give back" — and they shipped as one undifferentiated
    // column: same width, same type, one hairline between them, so the section
    // read as seven rows of one list. Grouping the totals is what lets the
    // stylesheet treat them as a block, and it is what a screen reader
    // announces before the numbers instead of running them into the list.
    const d = data({
      limits: { count: 2, cpu: 4, memory_bytes: 2048, disk_bytes: 1024 },
    });
    render(<MyResourcesPage client={client({ get: vi.fn(async () => d) })} />, { wrapper: Wrap });

    const live = await screen.findByRole("region", { name: "執行環境" });
    const totals = within(live).getByRole("group", { name: "目前合計" });
    expect(within(totals).getAllByRole("progressbar")).toHaveLength(3);
    // …and none of them inside the list, where every entry must be a thing the
    // Close button can act on.
    expect(within(within(live).getByRole("list")).queryByRole("progressbar")).toBeNull();
  });

  it("says how to free disk, because deleting happens in the item", async () => {
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    expect(await screen.findByText(/刪除永遠不受額度限制/)).toBeTruthy();
  });

  it("deletes an item and everything it owns from the disk row, after the cascade dialog", async () => {
    // The page is where the person AT their limit sees which item eats the
    // quota — so the delete lives here too, behind a dialog that says what
    // dies, and the myResources query refetches so the gauge visibly drops.
    const c = client();
    render(<MyResourcesPage client={c} />, { wrapper: Wrap });
    fireEvent.click(await screen.findByRole("button", { name: /刪除 Line 3 stoppage/ }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/檔案、對話、workflow 紀錄/);
    expect(dialog).toHaveTextContent(/知識庫的知識會保留/);
    fireEvent.click(within(dialog).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(api.deleteAppItem).toHaveBeenCalledWith("rca", "i-1"));
    await waitFor(() => expect(vi.mocked(c.get).mock.calls.length).toBeGreaterThan(1));
  });

  it("cancelling the disk-row delete dialog deletes nothing", async () => {
    vi.mocked(api.deleteAppItem).mockClear();
    render(<MyResourcesPage client={client()} />, { wrapper: Wrap });
    fireEvent.click(await screen.findByRole("button", { name: /刪除 Line 3 stoppage/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.deleteAppItem).not.toHaveBeenCalled();
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

describe("a close that could not be done", () => {
  afterEach(cleanup);

  it("says so on the row it failed on, which is still there to press again", async () => {
    // The backend raises rather than answering 204 when it cannot shut the
    // sandbox down right now (a reachable-but-slow host is 503 + Retry-After).
    // Rendering nothing for that is what made this button unreliable: the
    // person is told it worked while it did not.
    const closeEnvironment = vi.fn(async () => {
      throw new Error("close environment failed: 503");
    });
    // Refetching WOULD empty the list, so "the row survived" can only be true
    // because the failure suppressed the refetch — with a constant `get` the
    // assertion could not fail and would measure nothing.
    const get = vi
      .fn<() => Promise<MyResources>>()
      .mockResolvedValueOnce(data())
      .mockResolvedValue(data({ live: [], cpu_in_use: 0, memory_in_use: 0 }));

    render(<MyResourcesPage client={client({ get, closeEnvironment })} />, {
      wrapper: Wrap,
    });
    await userEvent.click(await screen.findByRole("button", { name: "關閉" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/關不掉/);
    // …inside the row it is about, so with several environments the reader can
    // tell which press failed
    expect(alert.closest("li")).not.toBeNull();
    expect(screen.getByRole("button", { name: "關閉" })).toBeTruthy();
  });

  it("marks only the row that failed, not every row", async () => {
    const closeEnvironment = vi.fn(async () => {
      throw new Error("close environment failed: 503");
    });
    const two = data({
      live: [
        { item_id: "i-1", slug: "rca", title: "Line 3 stoppage", cpu_cores: 1, memory_bytes: 800 },
        { item_id: "i-2", slug: "rca", title: "Line 9 stoppage", cpu_cores: 1, memory_bytes: 800 },
      ],
    });
    render(
      <MyResourcesPage client={client({ get: vi.fn(async () => two), closeEnvironment })} />,
      { wrapper: Wrap },
    );
    const buttons = await screen.findAllByRole("button", { name: "關閉" });
    await userEvent.click(buttons[1]);

    const alerts = await screen.findAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(within(alerts[0].closest("li")!).getByText("Line 9 stoppage")).toBeTruthy();
  });

  it("leaves the OTHER rows pressable while one close is in flight", async () => {
    // The sibling of the assertion above, for the pending state rather than the
    // failed one. One `useMutation` shared by every row made `isPending` a
    // property of the PAGE: pressing Close on one row greyed out every other
    // row's button until that request came back — on the page whose entire job
    // is getting somebody back under their limit, and where a close that has to
    // reach a busy host is exactly the slow case (503 + Retry-After).
    let release: () => void = () => {};
    const closeEnvironment = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const two = data({
      live: [
        { item_id: "i-1", slug: "rca", title: "Line 3 stoppage", cpu_cores: 1, memory_bytes: 800 },
        { item_id: "i-2", slug: "rca", title: "Line 9 stoppage", cpu_cores: 1, memory_bytes: 800 },
      ],
    });
    render(
      <MyResourcesPage client={client({ get: vi.fn(async () => two), closeEnvironment })} />,
      { wrapper: Wrap },
    );
    // Scoped to the live region: the same item is listed again under Storage,
    // so an unscoped lookup by title matches two rows in two different lists.
    const live = () => screen.getByRole("region", { name: "執行環境" });
    const closeIn = (title: string) =>
      within(within(live()).getByText(title).closest("li")!).getByRole("button", { name: "關閉" });

    await waitFor(() => expect(closeIn("Line 3 stoppage")).toBeTruthy());
    await userEvent.click(closeIn("Line 3 stoppage"));

    await waitFor(() => expect(closeIn("Line 3 stoppage")).toBeDisabled());
    expect(closeIn("Line 9 stoppage")).toBeEnabled();
    release();
  });
});

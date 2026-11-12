// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import type { CopyUpdate, CopyUpdateApi } from "../api/publish";
import { translate } from "../lib/i18n";
import { renderWithQuery } from "../test/queryWrapper";
import { CopyUpdateBanner } from "./CopyUpdateBanner";

// docs/plan-wui-copy-update.md: a WUI copy that is behind its publication says
// so at the top (D3), with the notes since (D6), and its person takes the new
// version (D1) — or, for a shared copy, goes to the current one (D2).
const who = vi.hoisted(() => ({ state: { id: "amy", ready: true } }));
vi.mock("../hooks/useCurrentUser", () => ({
  useCurrentUser: () => who.state.id,
  useCurrentUserState: () => who.state,
}));

const word = (key: string, vars?: Record<string, string | number>) => translate("zh-TW", key as never, vars);
const T0 = Date.UTC(2026, 10, 12, 9);
const AT = Date.UTC(2026, 10, 10, 9);

const due = (over: Partial<CopyUpdate> = {}): CopyUpdate => ({
  due: true,
  sharing: "per_person",
  published_at: AT,
  source_item_id: "rca-investigation:src",
  path: "/pages/report/page.ai.yaml",
  notes: [{ at: AT, by: "bob", text: "Totals per week.\nAnd a chart." }],
  needs: 100,
  free: null,
  free_if_replaced: null,
  fits: true,
  ...over,
});

function Where() {
  const loc = useLocation();
  return <output data-testid="at">{loc.pathname}</output>;
}

function mount(state: CopyUpdate, where: "page" | "workspace" = "page") {
  const client: CopyUpdateApi = {
    status: vi.fn(async () => state),
    take: vi.fn(async () => ({ slug: "rca", item_id: "rca-investigation:new" })),
  };
  renderWithQuery(
    <MemoryRouter initialEntries={["/here"]}>
      <Routes>
        <Route
          path="/here"
          element={<CopyUpdateBanner slug="rca" itemId="rca-investigation:old" where={where} client={client} />}
        />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return client;
}

// Let the answer arrive and render: a "not shown" read before it is no reading.
const settled = () => new Promise((r) => setTimeout(r, 30));

const banner = () => screen.findByRole("region", { name: word("copyUpdate.label") });

beforeEach(() => {
  localStorage.clear();
  who.state = { id: "amy", ready: true };
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(T0);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("CopyUpdateBanner", () => {
  it("says the page has a new version and what changed, with text actions and no lone close", async () => {
    mount(due());

    const b = await banner();

    expect(b).toHaveTextContent("Totals per week.");
    expect(b).not.toHaveTextContent("And a chart.");
    expect(within(b).getByRole("button", { name: word("copyUpdate.take") })).toBeInTheDocument();
    expect(within(b).getByRole("button", { name: word("copyUpdate.notes") })).toBeInTheDocument();
    expect(within(b).getByRole("button", { name: word("skills.updates.later") })).toBeInTheDocument();
    expect(within(b).queryByRole("button", { name: /×|close|關閉/i })).toBeNull();
  });

  it("keeps to two text buttons on its line, whatever the state (Material Design Banners)", async () => {
    // Review round 1: the line carried four.
    for (const state of [
      due(),
      due({ fits: false, needs: 2048, free: 1024, free_if_replaced: 4096 }),
      due({ sharing: "shared", needs: 0 }),
    ]) {
      localStorage.clear();
      mount(state);
      const line = await screen.findByTestId("copy-update-actions");
      const actions = within(line)
        .getAllByRole("button")
        .filter((el) => el.textContent !== word("copyUpdate.notes"));
      expect(actions.length).toBeLessThanOrEqual(2);
      cleanup();
    }
  });

  it("a shared copy's message carries the note too, and says nothing it does not know", async () => {
    mount(due({ sharing: "shared", needs: 0 }));
    const b = await banner();

    expect(b).toHaveTextContent("Totals per week.");
    expect(b).not.toHaveTextContent("其他人在那裡");
  });

  it("says why taking it failed in words, not the server's", async () => {
    const client = mount(due());
    vi.mocked(client.take).mockRejectedValueOnce(new HttpError(410, "the source of /p is gone"));
    const b = await banner();

    within(b).getByRole("button", { name: word("copyUpdate.take") }).click();

    expect(await within(b).findByRole("alert")).toHaveTextContent(word("copyUpdate.failed.gone"));
    expect(b).not.toHaveTextContent("the source of");
  });

  it("when the new copy was made but the old one's delete did not finish, says where the leftover is", async () => {
    const client = mount(due({ fits: false, needs: 2048, free: 1024, free_if_replaced: 4096 }));
    vi.mocked(client.take).mockRejectedValueOnce(new HttpError(500, "deleting the item's storage failed partway"));
    vi.mocked(client.status).mockResolvedValue({ ...due(), due: false });
    const b = await banner();

    within(b).getByRole("button", { name: word("copyUpdate.replace") }).click();
    within(await screen.findByRole("dialog")).getByRole("button", { name: word("copyUpdate.replace") }).click();

    expect(await within(b).findByText(word("copyUpdate.failed.partial"))).toBeInTheDocument();
  });

  it("says nothing for a copy that is current", async () => {
    const client = mount({ ...due(), due: false });

    await vi.waitFor(() => expect(client.status).toHaveBeenCalled());
    await settled();
    expect(screen.queryByRole("region", { name: word("copyUpdate.label") })).toBeNull();
  });

  it("takes the new version and lands on the same page in the new copy", async () => {
    const client = mount(due());

    within(await banner()).getByRole("button", { name: word("copyUpdate.take") }).click();

    expect(await screen.findByTestId("at")).toHaveTextContent(
      "/w/rca/rca-investigation%3Anew/pages/report/page.ai.yaml",
    );
    expect(client.take).toHaveBeenCalledWith("rca", "rca-investigation:old", { replace: false });
  });

  it("in the workspace, lands in the new copy's workspace", async () => {
    mount(due(), "workspace");

    within(await banner()).getByRole("button", { name: word("copyUpdate.take") }).click();

    expect(await screen.findByTestId("at")).toHaveTextContent("/a/rca/rca-investigation%3Anew");
  });

  it("shows every note since the copy on 看說明", async () => {
    mount(due({ notes: [{ at: AT - 1, by: "bob", text: "First." }, ...due().notes] }));
    const b = await banner();

    within(b).getByRole("button", { name: word("copyUpdate.notes") }).click();

    expect(await within(b).findByText(/And a chart\./)).toBeInTheDocument();
    expect(within(b).getByText(/First\./)).toBeInTheDocument();
  });

  it("waits for the signed-in user before judging the reminder (#997)", async () => {
    who.state = { id: "default-user", ready: false };
    const client = mount(due());

    await vi.waitFor(() => expect(client.status).toHaveBeenCalled());
    await settled();
    expect(screen.queryByRole("region", { name: word("copyUpdate.label") })).toBeNull();
  });

  it("remind me later hides it for 7 days; then it offers to skip this version", async () => {
    mount(due());
    const first = await banner();
    expect(within(first).queryByRole("button", { name: word("skills.updates.skip") })).toBeNull();
    within(first).getByRole("button", { name: word("skills.updates.later") }).click();
    await vi.waitFor(() => expect(screen.queryByRole("region", { name: word("copyUpdate.label") })).toBeNull());
    cleanup();

    vi.setSystemTime(T0 + 7 * 24 * 3600 * 1000 + 1);
    mount(due());
    const b = await banner();
    // Skip lives with the notes, as the skill reminder's does.
    within(b).getByRole("button", { name: word("copyUpdate.notes") }).click();
    (await within(b).findByRole("button", { name: word("skills.updates.skip") })).click();
    await vi.waitFor(() => expect(screen.queryByRole("region", { name: word("copyUpdate.label") })).toBeNull());
  });

  it("a shared copy goes to the current one instead of making a copy", async () => {
    const client = mount(due({ sharing: "shared", needs: 0 }));
    const b = await banner();

    expect(within(b).queryByRole("button", { name: word("copyUpdate.take") })).toBeNull();
    within(b).getByRole("button", { name: word("copyUpdate.goTo") }).click();

    expect(await screen.findByTestId("at")).toHaveTextContent(
      "/open/rca/rca-investigation%3Asrc/pages/report/page.ai.yaml",
    );
    expect(client.take).not.toHaveBeenCalled();
  });

  it("without room it says by how much, and deleting this copy in its place is confirmed first", async () => {
    const client = mount(due({ fits: false, needs: 2048, free: 1024, free_if_replaced: 4096 }));
    const b = await banner();

    expect(b).toHaveTextContent(word("copyUpdate.noRoom", { needs: "2.0 KB", free: "1.0 KB" }));
    expect(within(b).queryByRole("button", { name: word("copyUpdate.take") })).toBeNull();
    within(b).getByRole("button", { name: word("copyUpdate.replace") }).click();

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(word("copyUpdate.replaceBody"));
    within(dialog).getByRole("button", { name: word("copyUpdate.cancel") }).click();
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(client.take).not.toHaveBeenCalled();

    within(b).getByRole("button", { name: word("copyUpdate.replace") }).click();
    within(await screen.findByRole("dialog")).getByRole("button", { name: word("copyUpdate.replace") }).click();
    await vi.waitFor(() =>
      expect(client.take).toHaveBeenCalledWith("rca", "rca-investigation:old", { replace: true }),
    );
  });

  it("offers no delete when even that would not make room", async () => {
    mount(due({ fits: false, needs: 8192, free: 1024, free_if_replaced: 4096 }));
    const b = await banner();

    expect(b).toHaveTextContent(word("copyUpdate.noRoomAtAll"));
    expect(within(b).queryByRole("button", { name: word("copyUpdate.replace") })).toBeNull();
  });
});

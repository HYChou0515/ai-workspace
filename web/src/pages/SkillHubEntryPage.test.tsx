// @vitest-environment happy-dom
/**
 * One skill hub entry (`docs/plan-skill-hub.md`): the same page for everyone
 * who may read it, and the owner's five actions for the owner ALONE.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpError } from "../api/http";
import { makeQueryClient } from "../api/queryClient";
import { qk } from "../api/queryKeys";
import { currentWriteFailure, resetWriteFailures } from "../lib/writeFailures";
import type {
  SkillEditTarget,
  SkillHubApi,
  SkillHubDetail,
  SkillHubHistoryEvent,
} from "../api/skillHub";

vi.mock("../api", () => ({
  api: {
    listApps: vi.fn(async () => [
      { slug: "rca", title: "根因分析", description: "", icon: "flame", color: "#F0502E" },
    ]),
    getAppManifest: vi.fn(async () => ({ resource_route: "/rca-item" })),
    listAppItems: vi.fn(async () => [
      { resource_id: "i-9", title: "Line 3 reflow", owner: "bob", created_time: "", created_by: "bob" },
    ]),
    getUsers: vi.fn(async () => [
      { id: "alice", name: "Alice Wu", section: "", email: "", photo_url: null },
      { id: "bob", name: "Bob Lee", section: "", email: "", photo_url: null },
    ]),
  },
}));
vi.mock("../api/groups", () => ({
  groupsApi: {
    listPickableGroups: vi.fn(async () => [
      { resource_id: "g-qa", name: "QA", description: "", member_count: 3 },
    ]),
  },
}));

import { DialogProvider } from "../components/Dialog";
import { translate } from "../lib/i18n";
import { QueryWrap } from "../test/queryWrapper";
import { hubCard } from "../test/skillHubFake";
import { SkillHubEntryPage } from "./SkillHubEntryPage";

const word = (key: Parameters<typeof translate>[1], vars?: Record<string, string | number>) =>
  translate("zh-TW", key, vars);

const detail = (over: Partial<SkillHubDetail>): SkillHubDetail => ({
  id: "e-1",
  owner: "alice",
  name: "triage-reflow",
  description: "Triage reflow defects.",
  source_app: "rca",
  source_profile: "default",
  source_item: "",
  referenced_tools: ["exec", "read_file"],
  review: { verdict: "notes", notes: ["the description never says when", "step 2 needs exec"], model: "gpt-4o" },
  forked_from: null,
  forks: [],
  files: [
    { path: "SKILL.md", size: 80 },
    { path: "references/glossary.md", size: 12 },
  ],
  scripts: 0,
  skill_md: "---\nname: triage-reflow\ndescription: d\n---\n\n# How to triage\n\nRead the log.",
  is_owner: false,
  visibility: "public",
  permission: null,
  missing_tools: [],
  installs: 0,
  uses: 0,
  counted_since: "",
  updated_at: null,
  revision: "e-1:1",
  ...over,
});

const OWNED = detail({
  is_owner: true,
  source_item: "i-1",
  permission: {
    visibility: "public",
    read_meta: [],
    write_meta: [],
    read_content: [],
    add_content: [],
    edit_content: [],
    read_chat: [],
    converse: [],
    execute: [],
    use_terminal: [],
    change_permission: [],
  },
});

const OPEN: SkillEditTarget = { action: "open", app: "rca", profile: "default", item_id: "i-1", reason: "" };

const ev = (over: Partial<SkillHubHistoryEvent>): SkillHubHistoryEvent => ({
  revision: "e-1:1",
  kind: "publish",
  at: "2026-10-01T12:00:00Z",
  by: "alice",
  owner: "alice",
  commit: "c1",
  description: "Triage reflow defects.",
  review_notes: [],
  to_revision: "",
  visibility: "",
  audience: [],
  current: false,
  version: null,
  ...over,
});

/** Newest first: a transfer on top of a rollback to v1, over v2 and v1. */
const HISTORY: SkillHubHistoryEvent[] = [
  ev({ revision: "e-1:5", kind: "transfer", by: "bob", owner: "alice", commit: "c1", current: true, at: "2026-10-04T12:00:00Z" }),
  ev({ revision: "e-1:4", kind: "rollback", by: "bob", owner: "bob", commit: "c1", to_revision: "e-1:1", version: 3, at: "2026-10-03T12:00:00Z" }),
  ev({ revision: "e-1:2", kind: "publish", by: "bob", owner: "bob", commit: "c2", description: "v2 notes", version: 2, at: "2026-10-02T12:00:00Z" }),
  ev({ revision: "e-1:1", kind: "publish", by: "bob", owner: "bob", commit: "c1", version: 1, at: "2026-10-01T12:00:00Z" }),
];

/** How the page names a version (D8): `v N ・ date time`, local time. */
const vlabel = (n: number, iso: string) => {
  const d = new Date(iso);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `v${n} ・ ${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

function client(
  entry: SkillHubDetail,
  edit: SkillEditTarget = OPEN,
  history: SkillHubHistoryEvent[] = [ev({ current: true })],
) {
  return {
    list: vi.fn<SkillHubApi["list"]>(async () => []),
    browse: vi.fn<SkillHubApi["browse"]>(async () => ({ entries: [], total: 0, counted_since: "" })),
    get: vi.fn<SkillHubApi["get"]>(async () => entry),
    installs: vi.fn<SkillHubApi["installs"]>(async () => []),
    targets: vi.fn<SkillHubApi["targets"]>(async () => ({ missing_tools: [], items: [] })),
    install: vi.fn<SkillHubApi["install"]>(),
    unpublish: vi.fn<SkillHubApi["unpublish"]>(async () => undefined),
    republish: vi.fn<SkillHubApi["republish"]>(async () => undefined),
    setPermission: vi.fn<SkillHubApi["setPermission"]>(async () => undefined),
    remove: vi.fn<SkillHubApi["remove"]>(async () => undefined),
    transfer: vi.fn<SkillHubApi["transfer"]>(async () => undefined),
    edit: vi.fn<SkillHubApi["edit"]>(async () => edit),
    history: vi.fn<SkillHubApi["history"]>(async () => history),
    version: vi.fn<SkillHubApi["version"]>(async (_id, revision) => ({
      revision,
      commit: "c2",
      description: "v2 notes",
      files: [
        { path: "SKILL.md", size: 60 },
        { path: "shot.png", size: 9 },
        { path: "notes.md", size: 14 },
      ],
      scripts: 0,
      skill_md: "---\nname: triage-reflow\ndescription: d\n---\n\n# The second way\n",
    })),
    versionFile: vi.fn<SkillHubApi["versionFile"]>(async (_id, _rev, path) =>
      path === "shot.png" ? { path, text: null, size: 9 } : { path, text: "old notes body", size: 14 },
    ),
    diff: vi.fn<SkillHubApi["diff"]>(async () => [
      { path: "SKILL.md", status: "changed", patch: "@@ -1 +1 @@\n-old line\n+new line\n" },
      { path: "shot.png", status: "changed", patch: null },
    ]),
    rollback: vi.fn<SkillHubApi["rollback"]>(async () => undefined),
    fork: vi.fn<SkillHubApi["fork"]>(async () => ({ name: "triage-reflow", missing_tools: [] })),
  } satisfies SkillHubApi;
}

/** Mounted at its route, with the two places the page navigates to stubbed
 * so a navigation is observable. */
/** The list route, as the entry page leaves for it: prints whatever notice
 * rode along in the navigation state, so the hand-off is observable. */
function ListStub() {
  const notice = (useLocation().state as { notice?: { text: string } } | null)
    ?.notice;
  return <p>LIST PAGE {notice?.text ?? ""}</p>;
}

function mount(c: SkillHubApi, queryClient?: QueryClient, at = "/skill-hub/e-1") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <QueryWrap client={queryClient}>
        <DialogProvider>
          <Routes>
            <Route path="/skill-hub" element={<ListStub />} />
            <Route path="/skill-hub/:entryId" element={<SkillHubEntryPage client={c} />} />
            <Route path="/a/:slug/:itemId" element={<p>ITEM PAGE</p>} />
          </Routes>
        </DialogProvider>
      </QueryWrap>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

/** One of the owner's actions, from 「管理 ▾」 (plan-skill-hub-ux-redo D5). */
async function manage(key: Parameters<typeof translate>[1]) {
  fireEvent.click(await screen.findByRole("button", { name: word("skillHub.manage") }));
  return screen.getByRole("menuitem", { name: word(key) });
}

/** The 下架 confirm (D10): the impact sentence, then the confirm. */
async function confirmUnpublish() {
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent(word("skillHub.impact.installed"));
  fireEvent.click(within(dialog).getByRole("button", { name: word("skillHub.unpublish.confirm") }));
}

describe("SkillHubEntryPage", () => {
  it("shows a non-owner the skill's description tab, the sidebar — and of the actions only Install", async () => {
    mount(client(detail({})));

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(/^triage-reflow$/);
    expect(screen.getByText("Triage reflow defects.")).toBeInTheDocument();
    // The description tab is the default; it renders the body, not the frontmatter.
    expect(screen.getByRole("tab", { name: word("skillHub.tab.readme") })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("heading", { name: "How to triage" })).toBeInTheDocument();
    expect(screen.queryByText(/name: triage-reflow/)).toBeNull();
    // The sidebar: tools as plain text, the review notes, the source App once.
    const side = screen.getByRole("complementary");
    expect(within(side).getByText("exec")).toBeInTheDocument();
    expect(within(side).getByText("read_file")).toBeInTheDocument();
    expect(within(side).getByText("the description never says when")).toBeInTheDocument();
    expect(within(side).getByText(word("skillHub.review.by", { model: "gpt-4o" }))).toBeInTheDocument();
    expect(await within(side).findByText("根因分析")).toBeInTheDocument();
    // Installing happens here now (D6); none of the owner's actions.
    expect(screen.queryAllByRole("button").map((b) => b.textContent)).toEqual([
      word("skillHub.install"),
    ]);
  });

  it("says how many times it was installed and used, and since when (U6)", async () => {
    mount(client(detail({ installs: 4, uses: 17, counted_since: "2026-10-07" })));

    expect(
      await screen.findByText(word("skillHub.counts", { installs: 4, uses: 17 })),
    ).toBeInTheDocument();
    expect(screen.getByText(word("skillHub.countedSince", { day: "2026-10-07" }))).toBeInTheDocument();
  });

  it("gives the owner the five actions behind 「管理 ▾」, the delete last (D5)", async () => {
    mount(client(OWNED));

    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.manage") }));
    const names = screen.getAllByRole("menuitem").map((b) => b.textContent);
    expect(names).toEqual([
      word("skillHub.edit"),
      word("skillHub.unpublish"),
      word("skillHub.share"),
      word("skillHub.transfer"),
      word("skillHub.delete"),
    ]);
    expect(screen.getByRole("menuitem", { name: word("skillHub.delete") })).toHaveAttribute(
      "data-variant",
      "danger",
    );
    expect(
      within(screen.getByRole("complementary")).getByText(word("skillHub.visibility.public")),
    ).toBeInTheDocument();
  });

  it("a non-owner has no 「管理」", async () => {
    mount(client(detail({})));
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: word("skillHub.manage") })).toBeNull();
  });

  it("opens the visibility dialog with 'Public' meaning everyone on the platform (D12)", async () => {
    mount(client(OWNED));
    fireEvent.click(
      await manage("skillHub.share"),
    );
    const dialog = await screen.findByTestId("permission-dialog");
    expect(
      within(dialog).getByText(word("perm.public.hint.platform")),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText(word("perm.public.hint.workspace")),
    ).toBeNull();
  });

  it("unpublishes only after a confirm that says what happens to installed copies, then says it is done (D10)", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(await manage("skillHub.unpublish"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: word("skillHub.cancel") }));
    expect(c.unpublish).not.toHaveBeenCalled();

    fireEvent.click(await manage("skillHub.unpublish"));
    await confirmUnpublish();
    await waitFor(() => expect(c.unpublish).toHaveBeenCalledWith("e-1"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      word("skillHub.unpublished", { name: "triage-reflow" }),
    );

    cleanup();
    const c2 = client(detail({ ...OWNED, visibility: "private" }));
    mount(c2);
    fireEvent.click(await manage("skillHub.republish"));
    await waitFor(() => expect(c2.republish).toHaveBeenCalledWith("e-1"));
    expect(screen.getByText(word("skillHub.visibility.private"))).toBeInTheDocument();
  });

  it("deletes only after the confirm, then leaves for the list", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(await manage("skillHub.delete"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(word("skillHub.delete.title", { name: "triage-reflow" }));
    fireEvent.click(within(dialog).getByRole("button", { name: word("skillHub.cancel") }));
    expect(c.remove).not.toHaveBeenCalled();

    fireEvent.click(await manage("skillHub.delete"));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("skillHub.delete.confirm") }));
    await waitFor(() => expect(c.remove).toHaveBeenCalledWith("e-1"));
    expect(await screen.findByText(/^LIST PAGE/)).toBeInTheDocument();
  });

  it("Edit opens the source item when the server says open", async () => {
    mount(client(OWNED));
    fireEvent.click(await manage("skillHub.edit"));

    expect(await screen.findByText("ITEM PAGE")).toBeInTheDocument();
  });

  it("Edit explains a closed source item and points at a new one", async () => {
    mount(client(OWNED, { action: "new_item", app: "rca", profile: "default", item_id: "", reason: "closed" }));
    fireEvent.click(await manage("skillHub.edit"));

    const dialog = await screen.findByTestId("skill-hub-new-item");
    expect(dialog).toHaveTextContent(word("skillHub.edit.reason.closed"));
    // Review round 1: the profile the skill was written for rode along
    // nowhere; the new item opened on the App's default profile.
    expect(
      within(dialog).getByRole("link", { name: word("skillHub.edit.newItem.go") }),
    ).toHaveAttribute("href", "/a/rca/new?profile=default&skill=e-1");
  });

  it("transfers to the person picked", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(await manage("skillHub.transfer"));

    const dialog = await screen.findByTestId("skill-hub-transfer");
    const go = within(dialog).getByRole("button", { name: word("skillHub.transfer.confirm") });
    expect(go).toBeDisabled();
    fireEvent.click(await within(dialog).findByText("Bob Lee"));
    expect(go).toBeEnabled();
    fireEvent.click(go);
    await waitFor(() => expect(c.transfer).toHaveBeenCalledWith("e-1", "bob"));
    // Review round 1: after giving it away the page refetched an entry the
    // old owner may no longer read, and landed on the error line with a
    // Retry that could never succeed. It leaves for the list instead.
    expect(await screen.findByText(/^LIST PAGE/)).toBeInTheDocument();
  });

  it("says what a fork was forked from, including an original that went away", async () => {
    mount(client(detail({ forked_from: { entry: "e-root", state: "live", owner: "carol", name: "triage-reflow" } })));
    expect(await screen.findByRole("link", { name: word("skillHub.forkOf", { origin: "carol/triage-reflow" }) })).toHaveAttribute(
      "href",
      "/skill-hub/e-root",
    );

    cleanup();
    mount(client(detail({ forked_from: { entry: "e-root", state: "unpublished", owner: "", name: "" } })));
    expect(await screen.findByText(word("skillHub.origin.unpublished"))).toBeInTheDocument();

    cleanup();
    mount(client(detail({ forked_from: { entry: "e-root", state: "deleted", owner: "", name: "" } })));
    expect(await screen.findByText(word("skillHub.origin.deleted"))).toBeInTheDocument();
  });

  it("shows a failed action's reason instead of swallowing it", async () => {
    const c = client(OWNED);
    // The shape the real client throws (`api/skillHub.test.ts`).
    c.unpublish.mockRejectedValueOnce(new HttpError(403, "only the owner may manage this entry"));
    mount(c);
    fireEvent.click(await manage("skillHub.unpublish"));
    await confirmUnpublish();

    expect(await screen.findByRole("alert")).toHaveTextContent("only the owner may manage this entry");
  });

  it("words a coded refusal in the viewer's language (D16)", async () => {
    const c = client(OWNED);
    c.transfer.mockRejectedValueOnce(
      new HttpError(
        409,
        "transfer failed (409)",
        "transfer_name_taken",
        undefined,
        {
          error: "transfer_name_taken",
          owner: "bob",
          name: "triage-reflow",
        },
      ),
    );
    mount(c);
    fireEvent.click(
      await manage("skillHub.transfer"),
    );
    const dialog = await screen.findByTestId("skill-hub-transfer");
    fireEvent.click(await within(dialog).findByText("Bob Lee"));
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: word("skillHub.transfer.confirm"),
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      word("skillHub.refused.transfer_name_taken", {
        owner: "bob",
        name: "triage-reflow",
      }),
    );
  });

  // Every owner action shows its own failure line, so none may ALSO raise
  // the global write-failure toast (D3). Pinned per action — review round 1
  // of #826: the guard was applied on five mutations and pinned on one, so
  // dropping it from four of them reddened nothing.
  it.each([
    [
      "unpublish",
      "unpublish",
      async () => {
        fireEvent.click(
          await manage("skillHub.unpublish"),
        );
        await confirmUnpublish();
      },
    ],
    [
      "setPermission",
      "setPermission",
      async () => {
        fireEvent.click(
          await manage("skillHub.share"),
        );
        fireEvent.click(
          within(await screen.findByTestId("permission-dialog")).getByTestId(
            "permission-save",
          ),
        );
        // The dialog stays open, so the failure must be IN it — an alert on
        // the page behind the backdrop is one the person cannot see (round 2).
        return screen.getByTestId("permission-dialog");
      },
    ],
    [
      "transfer",
      "transfer",
      async () => {
        fireEvent.click(
          await manage("skillHub.transfer"),
        );
        const dialog = await screen.findByTestId("skill-hub-transfer");
        fireEvent.click(await within(dialog).findByText("Bob Lee"));
        fireEvent.click(
          within(dialog).getByRole("button", {
            name: word("skillHub.transfer.confirm"),
          }),
        );
        return dialog;
      },
    ],
    [
      "remove",
      "remove",
      async () => {
        fireEvent.click(
          await manage("skillHub.delete"),
        );
        fireEvent.click(
          within(await screen.findByRole("dialog")).getByRole("button", {
            name: word("skillHub.delete.confirm"),
          }),
        );
      },
    ],
    [
      "edit",
      "edit",
      async () => {
        fireEvent.click(
          await manage("skillHub.edit"),
        );
      },
    ],
  ] as const)(
    "does not ALSO raise the global write-failure toast for a failure it shows itself — %s (D3)",
    async (_name, method, act) => {
      resetWriteFailures();
      const c = client(OWNED);
      c[method].mockRejectedValueOnce(
        new HttpError(403, "only the owner may manage this entry"),
      );
      mount(c, makeQueryClient());
      const where = (await act()) ?? document.body;

      const alert = await within(where as HTMLElement).findByRole("alert");
      expect(alert).toHaveTextContent("only the owner may manage this entry");
      expect(currentWriteFailure()).toBeNull();
    },
  );

  it("leaves for the list WITH a notice after a transfer — who has it now, and that a private one is no longer yours to see (D10)", async () => {
    const c = client(detail({ ...OWNED, visibility: "private" }));
    mount(c);
    fireEvent.click(
      await manage("skillHub.transfer"),
    );
    const dialog = await screen.findByTestId("skill-hub-transfer");
    fireEvent.click(await within(dialog).findByText("Bob Lee"));
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: word("skillHub.transfer.confirm"),
      }),
    );

    await waitFor(() => expect(c.transfer).toHaveBeenCalledWith("e-1", "bob"));
    const list = await screen.findByText(/^LIST PAGE/);
    // the list stub prints the notice it was handed
    expect(list).toHaveTextContent(
      word("skillHub.transferred.private", {
        name: "triage-reflow",
        owner: "Bob Lee",
      }),
    );
  });

  it("clears a failure when the NEXT attempt starts, and a dialog shows only its own (review round 3)", async () => {
    // Round 2 cleared the failure when a dialog OPENED: a page-level failure
    // (unpublish) vanished for good on opening the share dialog, and a
    // share failure stayed on the page after the retry SUCCEEDED. The
    // failure belongs to the action that produced it: it clears when that
    // or another action starts, and a dialog draws only a failure of its own.
    const c = client(OWNED);
    c.unpublish.mockRejectedValueOnce(new HttpError(403, "only the owner may manage this entry"));
    c.setPermission.mockRejectedValueOnce(new HttpError(403, "only the owner may manage this entry"));
    mount(c);
    fireEvent.click(await manage("skillHub.unpublish"));
    await confirmUnpublish();
    await screen.findByRole("alert");

    // an unrelated dialog: the page's failure is not the dialog's, and it is back after
    fireEvent.click(await manage("skillHub.share"));
    const dialog = await screen.findByTestId("permission-dialog");
    expect(within(dialog).queryByRole("alert")).toBeNull();
    fireEvent.click(within(dialog).getByTestId("permission-cancel"));
    await waitFor(() => expect(screen.queryByTestId("permission-dialog")).toBeNull());
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // the share fails once, then succeeds: nothing stale stays behind
    fireEvent.click(await manage("skillHub.share"));
    const again = await screen.findByTestId("permission-dialog");
    fireEvent.click(within(again).getByTestId("permission-save"));
    await within(again).findByRole("alert");
    fireEvent.click(within(again).getByTestId("permission-save"));
    await waitFor(() => expect(screen.queryByTestId("permission-dialog")).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("leaves for the list WITH a notice after a delete (D10)", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(
      await manage("skillHub.delete"),
    );
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: word("skillHub.delete.confirm"),
      }),
    );

    await waitFor(() => expect(c.remove).toHaveBeenCalledWith("e-1"));
    const list = await screen.findByText(/^LIST PAGE/);
    expect(list).toHaveTextContent(
      word("skillHub.deleted", { name: "triage-reflow" }),
    );
  });
});

describe("SkillHubEntryPage layout (plan-skill-hub-ux-redo D3, D5, D6, D16)", () => {
  it("switches tabs through the address, the description first (D5)", async () => {
    mount(client(detail({ forks: [hubCard({ id: "e-f", owner: "bob", forked_from: "e-1" })] })));
    await screen.findByRole("heading", { name: "How to triage" });
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual([
      word("skillHub.tab.readme"),
      word("skillHub.tab.files", { count: 2 }),
      word("skillHub.tab.history"),
      word("skillHub.tab.forks", { count: 1 }),
    ]);

    fireEvent.click(screen.getByRole("tab", { name: word("skillHub.tab.forks", { count: 1 }) }));
    expect(await screen.findByTestId("entry-e-f")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "How to triage" })).toBeNull();
  });

  it("the files tab sums the files up and opens one through the current revision (D12)", async () => {
    const c = client(detail({}));
    mount(c, undefined, "/skill-hub/e-1?tab=files");
    expect(
      await screen.findByText(word("skillHub.files.summary", { count: 2, size: "92 B" })),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /SKILL\.md/ }));
    await waitFor(() => expect(c.versionFile).toHaveBeenCalledWith("e-1", "e-1:1", "SKILL.md"));
  });

  it("opens on the tab the address names — the list's fork count lands on the forks (D15)", async () => {
    mount(client(detail({ forks: [] })), undefined, "/skill-hub/e-1?tab=forks");
    expect(await screen.findByText(word("skillHub.forksOf.none"))).toBeInTheDocument();
  });

  it("the sidebar says when it was updated, the counts and since when (D5, U6)", async () => {
    mount(
      client(
        detail({ installs: 4, uses: 17, counted_since: "2026-10-07", updated_at: "2026-10-02T09:00:00Z" }),
      ),
    );
    const side = await screen.findByRole("complementary");
    expect(within(side).getByText(word("skillHub.counts", { installs: 4, uses: 17 }))).toBeInTheDocument();
    expect(within(side).getByText(word("skillHub.countedSince", { day: "2026-10-07" }))).toBeInTheDocument();
    expect(within(side).getByText("2026/10/02")).toBeInTheDocument();
  });

  it("lists the workspaces the viewer installed it in, each a link (D3)", async () => {
    const c = client(detail({}));
    c.installs.mockResolvedValue([{ app: "rca", item_id: "i-9", title: "Line 3 reflow" }]);
    mount(c);
    const side = await screen.findByRole("complementary");
    expect(await within(side).findByRole("link", { name: /Line 3 reflow/ })).toHaveAttribute(
      "href",
      "/a/rca/i-9",
    );
  });

  it("says nothing about installs it could not read — not 「還沒有裝」 (rollout)", async () => {
    const c = client(detail({}));
    c.installs.mockRejectedValue(new HttpError(404, "not found"));
    mount(c);
    const side = await screen.findByRole("complementary");
    await waitFor(() => expect(c.installs).toHaveBeenCalled());
    expect(within(side).queryByText(word("skillHub.installs.none"))).toBeNull();
  });

  it("installs into a workspace picked per App, saying beforehand what each would do, and stays on the page (D6)", async () => {
    const c = client(detail({}));
    c.targets.mockResolvedValue({
      missing_tools: ["read_file"],
      items: [
        { item_id: "i-1", title: "Free one", state: "ok", owner: "" },
        { item_id: "i-2", title: "Has it", state: "installed", owner: "" },
        { item_id: "i-3", title: "Clash", state: "name_taken", owner: "carol" },
        { item_id: "i-4", title: "Busy", state: "unavailable", owner: "" },
      ],
    });
    c.install.mockResolvedValue({ name: "triage-reflow", missing_tools: ["read_file"] });
    mount(c);

    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.install") }));
    const dialog = await screen.findByTestId("skill-hub-install");
    await waitFor(() => expect(c.targets).toHaveBeenCalledWith("e-1", "rca"));
    expect(await within(dialog).findByText(word("skillHub.install.missing", { tools: "read_file" }))).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /Has it/ })).toBeDisabled();
    expect(within(dialog).getByText(word("skillHub.install.state.installed"))).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /Clash/ })).toBeDisabled();
    expect(within(dialog).getByText(word("skillHub.install.state.taken", { owner: "carol" }))).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /Busy/ })).toBeDisabled();
    expect(within(dialog).getByText(word("skillHub.install.state.unavailable"))).toBeInTheDocument();
    const go = within(dialog).getByRole("button", { name: word("skillHub.install.confirm") });
    expect(go).toBeDisabled();

    fireEvent.click(within(dialog).getByRole("radio", { name: /Free one/ }));
    fireEvent.click(go);

    await waitFor(() => expect(c.install).toHaveBeenCalledWith("rca", "i-1", "e-1"));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(word("skillHub.install.done", { title: "Free one" }));
    expect(within(status).getByRole("link", { name: word("skillHub.install.open") })).toHaveAttribute(
      "href",
      "/a/rca/i-1",
    );
    expect(screen.queryByTestId("skill-hub-install")).toBeNull();
    await waitFor(() => expect(c.installs.mock.calls.length).toBeGreaterThan(1));
  });

  it("says the skill is not there — no Retry — when the server says not found (D16)", async () => {
    const c = client(detail({}));
    c.get.mockRejectedValue(new HttpError(404, "the skill hub entry could not be read (404)", "not_found"));
    mount(c);
    expect(await screen.findByText(word("skillHub.notFound"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: word("skillHub.backToList") })).toHaveAttribute(
      "href",
      "/skill-hub",
    );
    expect(screen.queryByRole("button", { name: word("skillHub.retry") })).toBeNull();
  });

  it("offers Retry when the page could not be reached (D16)", async () => {
    const c = client(detail({}));
    c.get.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    mount(c);
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.retry") }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "triage-reflow" }),
    ).toBeInTheDocument();
  });
});

describe("SkillHubEntryPage history (plan-skill-hub-history §8)", () => {
  const timeline = () => screen.findByRole("list", { name: word("skillHub.history") });

  it("says who a visibility change opened the skill to, by name (G24 「含名單」)", async () => {
    const history = [
      ev({
        revision: "e-1:3",
        kind: "permission",
        visibility: "restricted",
        audience: ["user:alice", "group:g-qa"],
        current: true,
      }),
      ev({ revision: "e-1:1", version: 1 }),
    ];
    mount(client(OWNED, OPEN, history), undefined, "/skill-hub/e-1?tab=history");

    const rows = within(await timeline()).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent(word("skillHub.history.audience", { who: "Alice Wu, QA" }));
    expect(rows[0]).not.toHaveTextContent("user:");
  });

  it("never prints a subject it cannot name: `all` is everyone, an unknown group is a group", async () => {
    const history = [
      ev({
        revision: "e-1:3",
        kind: "permission",
        visibility: "restricted",
        audience: ["all", "group:g-hidden"],
        current: true,
      }),
      ev({ revision: "e-1:1", version: 1 }),
    ];
    mount(client(OWNED, OPEN, history), undefined, "/skill-hub/e-1?tab=history");

    const rows = within(await timeline()).getAllByRole("listitem");
    const who = `${word("skillHub.history.audience.everyone")}, ${word("skillHub.history.audience.group")}`;
    expect(rows[0]).toHaveTextContent(word("skillHub.history.audience", { who }));
    expect(rows[0]).not.toHaveTextContent("g-hidden");
  });

  it("lists every row newest first, names each version v N with its time, and marks the current one (D8)", async () => {
    mount(client(detail({}), OPEN, HISTORY), undefined, "/skill-hub/e-1?tab=history");

    const rows = within(await timeline()).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    // A transfer is a one-line note, not a version.
    expect(rows[0]).toHaveTextContent(word("skillHub.history.kind.transfer", { owner: "Alice Wu" }));
    expect(rows[0]).not.toHaveTextContent(/v\d/);
    // The current VERSION is v3 — the transfer row changed no content.
    expect(rows[1]).toHaveTextContent(vlabel(3, "2026-10-03T12:00:00Z"));
    expect(rows[1]).toHaveTextContent(word("skillHub.history.current"));
    expect(rows[1]).toHaveTextContent(word("skillHub.history.kind.rollback", { version: "v1" }));
    expect(rows[2]).toHaveTextContent(vlabel(2, "2026-10-02T12:00:00Z"));
    expect(rows[2]).toHaveTextContent(word("skillHub.history.kind.publish"));
    // Only what changed: v2's description differs from v1's, so it shows; v1's is the first.
    expect(rows[2]).toHaveTextContent("v2 notes");
    // v3 brought v1's description back: it changed from v2's, so it shows too.
    expect(rows[1]).toHaveTextContent("Triage reflow defects.");
    // A transfer row changes no content: nothing to read, compare or fork.
    expect(within(rows[0]).queryAllByRole("button")).toEqual([]);
    // No internals on screen: the version's id is never printed.
    expect(screen.queryByText(/e-1:2|c2/)).toBeNull();
    // A non-owner may read, compare and fork an old version — never roll back.
    expect(within(rows[2]).getAllByRole("button").map((b) => b.textContent)).toEqual([
      word("skillHub.history.view"),
      word("skillHub.history.compare"),
      word("skillHub.history.fork"),
    ]);
  });

  it("offers no compare and no rollback on a row whose version is the current one", async () => {
    // The rollback (row 1) brought v1 back, so v1's own row (row 3) holds the
    // same content as now: comparing shows nothing, rolling back does nothing.
    mount(client(OWNED, OPEN, HISTORY), undefined, "/skill-hub/e-1?tab=history");
    const rows = within(await timeline()).getAllByRole("listitem");
    for (const row of [rows[1], rows[3]]) {
      expect(within(row).getAllByRole("button").map((b) => b.textContent)).toEqual([
        word("skillHub.history.view"),
        word("skillHub.history.fork"),
      ]);
    }
    expect(within(rows[2]).getAllByRole("button").map((b) => b.textContent)).toEqual([
      word("skillHub.history.view"),
      word("skillHub.history.compare"),
      word("skillHub.history.fork"),
      word("skillHub.history.rollback"),
    ]);
    // One primary action per screen (D14): rolling back is not it.
    expect(within(rows[2]).getByRole("button", { name: word("skillHub.history.rollback") })).toHaveAttribute(
      "data-variant",
      "secondary",
    );
  });

  it("shows the latest five rows, and the rest on request (D14)", async () => {
    const many = Array.from({ length: 8 }, (_, n) =>
      ev({
        revision: `e-1:${8 - n}`,
        commit: `c${8 - n}`,
        version: 8 - n,
        at: `2026-10-0${8 - n}T12:00:00Z`,
        current: n === 0,
      }),
    );
    mount(client(detail({}), OPEN, many), undefined, "/skill-hub/e-1?tab=history");
    const list = await timeline();
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);

    fireEvent.click(screen.getByRole("button", { name: word("skillHub.history.showAll", { count: 8 }) }));

    expect(within(list).getAllByRole("listitem")).toHaveLength(8);
  });

  it("shows an old version's SKILL.md and files, and one file's text or that it is not text", async () => {
    const c = client(detail({}), OPEN, HISTORY);
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.view") }));

    const modal = await screen.findByTestId("skill-hub-version");
    expect(within(modal).getByRole("heading", { level: 2 })).toHaveTextContent(
      vlabel(2, "2026-10-02T12:00:00Z"),
    );
    expect(await within(modal).findByRole("heading", { name: "The second way" })).toBeInTheDocument();
    expect(c.version).toHaveBeenCalledWith("e-1", "e-1:2");
    // The same files view as the files tab (D12).
    fireEvent.click(within(modal).getByRole("button", { name: /notes\.md/ }));
    expect(await within(modal).findByText("old notes body")).toBeInTheDocument();
    fireEvent.click(within(modal).getByRole("button", { name: /shot\.png/ }));
    expect(await within(modal).findByText(word("skillHub.files.notText"))).toBeInTheDocument();
  });

  it("compares an old version with the current one, file by file", async () => {
    const c = client(detail({}), OPEN, HISTORY);
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.compare") }));

    const modal = await screen.findByTestId("skill-hub-diff");
    // Titled by the two versions; the current is v3 (the transfer row is not a version).
    expect(within(modal).getByRole("heading", { level: 2 })).toHaveTextContent("v2 → v3");
    expect(c.diff).toHaveBeenCalledWith("e-1", "e-1:2", "e-1:5");
    // A summary first, then each file on request.
    expect(
      await within(modal).findByText(word("skillHub.history.diff.summary", { added: 0, changed: 2, removed: 0 })),
    ).toBeInTheDocument();
    expect(within(modal).queryByText("new line")).toBeNull();
    fireEvent.click(within(modal).getByRole("button", { name: /SKILL\.md/ }));
    const added = await within(modal).findByText("new line");
    expect(added.closest("[data-line]")).toHaveAttribute("data-line", "add");
    expect(within(modal).getByText("old line").closest("[data-line]")).toHaveAttribute("data-line", "del");
    // The raw headers are not shown.
    expect(within(modal).queryByText(/@@|^\+\+\+|^---/)).toBeNull();
    fireEvent.click(within(modal).getByRole("button", { name: /shot\.png/ }));
    expect(within(modal).getByText(word("skillHub.history.binaryChanged"))).toBeInTheDocument();
  });

  it("drops only the file header: a removed line reading `-- x` is content (D13)", async () => {
    const c = client(detail({}), OPEN, HISTORY);
    c.diff.mockResolvedValue([
      {
        path: "notes.md",
        status: "changed",
        patch: "--- a/notes.md\n+++ b/notes.md\n@@ -1,2 +1,2 @@\n--- a dashed note\n+++ a plus note\n same\n@@ -9 +9 @@\n-x\n+y\n",
      },
    ]);
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.compare") }));
    const modal = await screen.findByTestId("skill-hub-diff");
    fireEvent.click(await within(modal).findByRole("button", { name: /notes\.md/ }));

    expect(within(modal).getByText("-- a dashed note").closest("[data-line]")).toHaveAttribute("data-line", "del");
    expect(within(modal).getByText("++ a plus note").closest("[data-line]")).toHaveAttribute("data-line", "add");
    expect(within(modal).queryByText(/a\/notes\.md|b\/notes\.md/)).toBeNull();
    // the second hunk is set off by a gap, the first is not
    expect(modal.querySelectorAll("[data-line=gap]")).toHaveLength(1);
  });

  it("lets the owner roll back, against the version the page showed", async () => {
    const c = client(OWNED, OPEN, HISTORY);
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.rollback") }));
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.history.rollback.confirm") }));

    await waitFor(() => expect(c.rollback).toHaveBeenCalledWith("e-1", "e-1:2", "c1"));
  });

  it("says the version moved when someone changed it meanwhile, and reloads the history", async () => {
    const c = client(OWNED, OPEN, HISTORY);
    c.rollback.mockRejectedValueOnce(
      new HttpError(409, "rollback failed (409)", "version_moved", [], {}),
    );
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.rollback") }));
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.history.rollback.confirm") }));

    expect(
      await screen.findByText(word("skillHub.failed", { reason: word("skillHub.history.moved") })),
    ).toBeInTheDocument();
    await waitFor(() => expect(c.history).toHaveBeenCalledTimes(2));
  });

  it("forks a version into one of the viewer's items", async () => {
    const c = client(detail({}), OPEN, HISTORY);
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.fork") }));

    const dialog = await screen.findByTestId("skill-hub-fork");
    const go = within(dialog).getByRole("button", { name: word("skillHub.history.fork.confirm") });
    expect(go).toBeDisabled();
    fireEvent.click(await within(dialog).findByRole("radio", { name: /Line 3 reflow/ }));
    fireEvent.click(go);

    await waitFor(() => expect(c.fork).toHaveBeenCalledWith("rca", "i-9", "e-1", "e-1:2"));
    expect(await within(dialog).findByRole("link", { name: word("skillHub.history.fork.open") })).toHaveAttribute(
      "href",
      "/a/rca/i-9",
    );
  });

  it("shows what the review said about each version on its row (§8)", async () => {
    const history = HISTORY.map((e) =>
      e.revision === "e-1:2"
        ? { ...e, review_notes: ["names a path it does not ship"] }
        : e.revision === "e-1:4"
          ? { ...e, review_notes: ["the current version's note"] }
          : e,
    );
    mount(client(detail({}), OPEN, history), undefined, "/skill-hub/e-1?tab=history");
    const list = await timeline();
    // The rows themselves — a row's notes are list items of their own list.
    const rows = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
    expect(rows).toHaveLength(4);
    expect(rows[2]).toHaveTextContent("names a path it does not ship");
    expect(rows[3]).not.toHaveTextContent("names a path");
    // The current version's notes are the sidebar's; said once (D17).
    expect(rows[1]).not.toHaveTextContent("the current version's note");
  });

  it("compares a version with any other one, the current by default (§8 「能和另一版比對」)", async () => {
    const three: SkillHubHistoryEvent[] = [
      ev({ revision: "e-1:3", commit: "c3", at: "2026-10-03T12:00:00Z", current: true, version: 3 }),
      ev({ revision: "e-1:2", commit: "c2", at: "2026-10-02T12:00:00Z", version: 2 }),
      ev({ revision: "e-1:1", commit: "c1", at: "2026-10-01T12:00:00Z", version: 1 }),
    ];
    const c = client(detail({}), OPEN, three);
    mount(c, undefined, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[1];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.compare") }));

    const modal = await screen.findByTestId("skill-hub-diff");
    await waitFor(() => expect(c.diff).toHaveBeenCalledWith("e-1", "e-1:2", "e-1:3"));
    const against = within(modal).getByRole("combobox", { name: word("skillHub.history.diff.against") });
    // The other versions, by when they were published; never the version itself.
    expect(within(against).getAllByRole("option").map((o) => o.textContent)).toEqual([
      `${vlabel(3, "2026-10-03T12:00:00Z")}（${word("skillHub.history.current")}）`,
      vlabel(1, "2026-10-01T12:00:00Z"),
    ]);
    fireEvent.change(against, { target: { value: "e-1:1" } });
    await waitFor(() => expect(c.diff).toHaveBeenCalledWith("e-1", "e-1:2", "e-1:1"));
  });

  it("says what a compare without a line diff means, for both causes", () => {
    expect(word("skillHub.history.binaryChanged")).toMatch(/太大/);
    expect(word("skillHub.history.binaryChanged")).toMatch(/不是文字檔/);
    expect(translate("en", "skillHub.history.binaryChanged")).toMatch(/too large/i);
  });

  it("a fork refreshes the item's skills, as an install does", async () => {
    const qc = makeQueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const c = client(detail({}), OPEN, HISTORY);
    mount(c, qc, "/skill-hub/e-1?tab=history");
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.fork") }));
    const dialog = await screen.findByTestId("skill-hub-fork");
    fireEvent.click(await within(dialog).findByRole("radio", { name: /Line 3 reflow/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: word("skillHub.history.fork.confirm") }));

    await within(dialog).findByRole("link", { name: word("skillHub.history.fork.open") });
    expect(spy).toHaveBeenCalledWith({ queryKey: qk.itemSkills("rca", "i-9") });
  });
});


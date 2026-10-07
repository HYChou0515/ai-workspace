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
  groupsApi: { listPickableGroups: vi.fn(async () => []) },
}));

import { DialogProvider } from "../components/Dialog";
import { translate } from "../lib/i18n";
import { QueryWrap } from "../test/queryWrapper";
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
  files: ["SKILL.md", "references/glossary.md"],
  skill_md: "---\nname: triage-reflow\ndescription: d\n---\n\n# How to triage\n\nRead the log.",
  is_owner: false,
  visibility: "public",
  permission: null,
  missing_tools: [],
  installs: 0,
  uses: 0,
  counted_since: "",
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
  current: false,
  ...over,
});

/** Newest first: a transfer on top of a rollback to v1, over v2 and v1. */
const HISTORY: SkillHubHistoryEvent[] = [
  ev({ revision: "e-1:5", kind: "transfer", by: "bob", owner: "alice", commit: "c1", current: true }),
  ev({ revision: "e-1:4", kind: "rollback", by: "bob", owner: "bob", commit: "c1", to_revision: "e-1:1" }),
  ev({ revision: "e-1:2", kind: "publish", by: "bob", owner: "bob", commit: "c2", description: "v2 notes" }),
  ev({ revision: "e-1:1", kind: "publish", by: "bob", owner: "bob", commit: "c1" }),
];

function client(
  entry: SkillHubDetail,
  edit: SkillEditTarget = OPEN,
  history: SkillHubHistoryEvent[] = [ev({ current: true })],
) {
  return {
    list: vi.fn<SkillHubApi["list"]>(async () => []),
    browse: vi.fn<SkillHubApi["browse"]>(async () => ({ entries: [], counted_since: "" })),
    get: vi.fn<SkillHubApi["get"]>(async () => entry),
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
      files: ["SKILL.md", "shot.png", "notes.md"],
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

function mount(c: SkillHubApi, queryClient?: QueryClient) {
  return render(
    <MemoryRouter initialEntries={["/skill-hub/e-1"]}>
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

describe("SkillHubEntryPage", () => {
  it("shows a non-owner the skill, the tools, the review notes — and not one button", async () => {
    mount(client(detail({})));

    expect(await screen.findByRole("heading", { level: 1, name: /alice/ })).toHaveTextContent("alice/triage-reflow");
    expect(screen.getByText("Triage reflow defects.")).toBeInTheDocument();
    expect(screen.getByText("exec")).toBeInTheDocument();
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("the description never says when")).toBeInTheDocument();
    expect(screen.getByText(word("skillHub.review.by", { model: "gpt-4o" }))).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "How to triage" })).toBeInTheDocument();
    // The body, not the frontmatter: rendered, `---` under a line makes a heading.
    expect(screen.queryByText(/name: triage-reflow/)).toBeNull();
    expect(screen.getByText("references/glossary.md")).toBeInTheDocument();
    // Installing is the item's (D2): a sentence, never a control.
    expect(screen.getByText(word("skillHub.howToInstall"))).toBeInTheDocument();
    // None of the owner's actions; the history's own controls are everyone's
    // (plan-skill-hub-history §8) — on a one-version entry, just the fork.
    await screen.findByText(word("skillHub.history.current"));
    expect(screen.queryAllByRole("button").map((b) => b.textContent)).toEqual([
      word("skillHub.history.fork"),
    ]);
  });

  it("says how many times it was installed and used, and since when (U6)", async () => {
    mount(client(detail({ installs: 4, uses: 17, counted_since: "2026-10-07" })));

    expect(
      await screen.findByText(word("skillHub.counts", { installs: 4, uses: 17 })),
    ).toBeInTheDocument();
    expect(screen.getByText(word("skillHub.countedSince", { day: "2026-10-07" }))).toBeInTheDocument();
  });

  it("gives the owner the five actions, and no install sentence", async () => {
    mount(client(OWNED));

    await screen.findByRole("heading", { level: 1, name: /alice/ });
    const actions = screen.getByRole("group", { name: word("skillHub.edit") });
    const names = within(actions).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual([
      word("skillHub.edit"),
      word("skillHub.unpublish"),
      word("skillHub.share"),
      word("skillHub.transfer"),
      word("skillHub.delete"),
    ]);
    expect(screen.queryByText(word("skillHub.howToInstall"))).toBeNull();
    expect(screen.getByText(word("skillHub.visibility.public"))).toBeInTheDocument();
  });

  it("opens the visibility dialog with 'Public' meaning everyone on the platform (D12)", async () => {
    mount(client(OWNED));
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.share") }),
    );
    const dialog = await screen.findByTestId("permission-dialog");
    expect(
      within(dialog).getByText(word("perm.public.hint.platform")),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText(word("perm.public.hint.workspace")),
    ).toBeNull();
  });

  it("unpublishes, and on a private entry offers Republish instead", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.unpublish") }));
    await waitFor(() => expect(c.unpublish).toHaveBeenCalledWith("e-1"));

    cleanup();
    const c2 = client(detail({ ...OWNED, visibility: "private" }));
    mount(c2);
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.republish") }));
    await waitFor(() => expect(c2.republish).toHaveBeenCalledWith("e-1"));
    expect(screen.getByText(word("skillHub.visibility.private"))).toBeInTheDocument();
  });

  it("deletes only after the confirm, then leaves for the list", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.delete") }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(word("skillHub.delete.title", { name: "triage-reflow" }));
    fireEvent.click(within(dialog).getByRole("button", { name: word("skillHub.cancel") }));
    expect(c.remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: word("skillHub.delete") }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: word("skillHub.delete.confirm") }));
    await waitFor(() => expect(c.remove).toHaveBeenCalledWith("e-1"));
    expect(await screen.findByText(/^LIST PAGE/)).toBeInTheDocument();
  });

  it("Edit opens the source item when the server says open", async () => {
    mount(client(OWNED));
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.edit") }));

    expect(await screen.findByText("ITEM PAGE")).toBeInTheDocument();
  });

  it("Edit explains a closed source item and points at a new one", async () => {
    mount(client(OWNED, { action: "new_item", app: "rca", profile: "default", item_id: "", reason: "closed" }));
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.edit") }));

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
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.transfer") }));

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
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.unpublish") }));

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
      await screen.findByRole("button", { name: word("skillHub.transfer") }),
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
          await screen.findByRole("button", {
            name: word("skillHub.unpublish"),
          }),
        );
      },
    ],
    [
      "setPermission",
      "setPermission",
      async () => {
        fireEvent.click(
          await screen.findByRole("button", { name: word("skillHub.share") }),
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
          await screen.findByRole("button", {
            name: word("skillHub.transfer"),
          }),
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
          await screen.findByRole("button", { name: word("skillHub.delete") }),
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
          await screen.findByRole("button", { name: word("skillHub.edit") }),
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
      await screen.findByRole("button", { name: word("skillHub.transfer") }),
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
    fireEvent.click(await screen.findByRole("button", { name: word("skillHub.unpublish") }));
    await screen.findByRole("alert");

    // an unrelated dialog: the page's failure is not the dialog's, and it is back after
    fireEvent.click(screen.getByRole("button", { name: word("skillHub.share") }));
    const dialog = await screen.findByTestId("permission-dialog");
    expect(within(dialog).queryByRole("alert")).toBeNull();
    fireEvent.click(within(dialog).getByTestId("permission-cancel"));
    await waitFor(() => expect(screen.queryByTestId("permission-dialog")).toBeNull());
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // the share fails once, then succeeds: nothing stale stays behind
    fireEvent.click(screen.getByRole("button", { name: word("skillHub.share") }));
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
      await screen.findByRole("button", { name: word("skillHub.delete") }),
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

describe("SkillHubEntryPage history (plan-skill-hub-history §8)", () => {
  const timeline = () => screen.findByRole("list", { name: word("skillHub.history") });

  it("lists every row newest first, says what each did, and marks the current one", async () => {
    mount(client(detail({}), OPEN, HISTORY));

    const rows = within(await timeline()).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent(word("skillHub.history.kind.transfer", { owner: "Alice Wu" }));
    expect(rows[0]).toHaveTextContent(word("skillHub.history.current"));
    expect(rows[1]).toHaveTextContent(word("skillHub.history.kind.rollback", { when: "2026/10/01" }));
    expect(rows[2]).toHaveTextContent(word("skillHub.history.kind.publish"));
    expect(rows[2]).toHaveTextContent("v2 notes");
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
    mount(client(OWNED, OPEN, HISTORY));
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
  });

  it("shows an old version's SKILL.md and files, and one file's text or that it is not text", async () => {
    const c = client(detail({}), OPEN, HISTORY);
    mount(c);
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.view") }));

    const modal = await screen.findByTestId("skill-hub-version");
    expect(await within(modal).findByRole("heading", { name: "The second way" })).toBeInTheDocument();
    expect(c.version).toHaveBeenCalledWith("e-1", "e-1:2");
    fireEvent.click(within(modal).getByRole("button", { name: "notes.md" }));
    expect(await within(modal).findByText("old notes body")).toBeInTheDocument();
    fireEvent.click(within(modal).getByRole("button", { name: "shot.png" }));
    expect(await within(modal).findByText(word("skillHub.history.notText"))).toBeInTheDocument();
  });

  it("compares an old version with the current one, file by file", async () => {
    const c = client(detail({}), OPEN, HISTORY);
    mount(c);
    const row = within(await timeline()).getAllByRole("listitem")[2];
    fireEvent.click(within(row).getByRole("button", { name: word("skillHub.history.compare") }));

    const modal = await screen.findByTestId("skill-hub-diff");
    expect(await within(modal).findByText(/\+new line/)).toBeInTheDocument();
    expect(within(modal).getByText(word("skillHub.history.binaryChanged"))).toBeInTheDocument();
    expect(c.diff).toHaveBeenCalledWith("e-1", "e-1:2", "e-1:5");
  });

  it("lets the owner roll back, against the version the page showed", async () => {
    const c = client(OWNED, OPEN, HISTORY);
    mount(c);
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
    mount(c);
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
    mount(c);
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
});


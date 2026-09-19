// @vitest-environment happy-dom
/**
 * One skill hub entry (`docs/plan-skill-hub.md`): the same page for everyone
 * who may read it, and the owner's five actions for the owner ALONE.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
} from "../api/skillHub";

vi.mock("../api", () => ({
  api: {
    listApps: vi.fn(async () => [
      {
        slug: "rca",
        title: "根因分析",
        description: "",
        icon: "flame",
        color: "#F0502E",
      },
    ]),
    getUsers: vi.fn(async () => [
      {
        id: "alice",
        name: "Alice Wu",
        section: "",
        email: "",
        photo_url: null,
      },
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

const word = (
  key: Parameters<typeof translate>[1],
  vars?: Record<string, string | number>,
) => translate("zh-TW", key, vars);

const detail = (over: Partial<SkillHubDetail>): SkillHubDetail => ({
  id: "e-1",
  owner: "alice",
  name: "triage-reflow",
  description: "Triage reflow defects.",
  source_app: "rca",
  source_profile: "default",
  source_item: "",
  referenced_tools: ["exec", "read_file"],
  review: {
    verdict: "notes",
    notes: ["the description never says when", "step 2 needs exec"],
    model: "gpt-4o",
  },
  forked_from: null,
  forks: [],
  files: ["SKILL.md", "references/glossary.md"],
  skill_md:
    "---\nname: triage-reflow\ndescription: d\n---\n\n# How to triage\n\nRead the log.",
  is_owner: false,
  visibility: "public",
  permission: null,
  missing_tools: [],
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

const OPEN: SkillEditTarget = {
  action: "open",
  app: "rca",
  profile: "default",
  item_id: "i-1",
  reason: "",
};

function client(entry: SkillHubDetail, edit: SkillEditTarget = OPEN) {
  return {
    list: vi.fn<SkillHubApi["list"]>(async () => []),
    get: vi.fn<SkillHubApi["get"]>(async () => entry),
    install: vi.fn<SkillHubApi["install"]>(),
    unpublish: vi.fn<SkillHubApi["unpublish"]>(async () => undefined),
    republish: vi.fn<SkillHubApi["republish"]>(async () => undefined),
    setPermission: vi.fn<SkillHubApi["setPermission"]>(async () => undefined),
    remove: vi.fn<SkillHubApi["remove"]>(async () => undefined),
    transfer: vi.fn<SkillHubApi["transfer"]>(async () => undefined),
    edit: vi.fn<SkillHubApi["edit"]>(async () => edit),
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
            <Route
              path="/skill-hub/:entryId"
              element={<SkillHubEntryPage client={c} />}
            />
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

    expect(
      await screen.findByRole("heading", { level: 1, name: /alice/ }),
    ).toHaveTextContent("alice/triage-reflow");
    expect(screen.getByText("Triage reflow defects.")).toBeInTheDocument();
    expect(screen.getByText("exec")).toBeInTheDocument();
    expect(screen.getByText("read_file")).toBeInTheDocument();
    expect(
      screen.getByText("the description never says when"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(word("skillHub.review.by", { model: "gpt-4o" })),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "How to triage" }),
    ).toBeInTheDocument();
    // The body, not the frontmatter: rendered, `---` under a line makes a heading.
    expect(screen.queryByText(/name: triage-reflow/)).toBeNull();
    expect(screen.getByText("references/glossary.md")).toBeInTheDocument();
    // Installing is the item's (D2): a sentence, never a control.
    expect(screen.getByText(word("skillHub.howToInstall"))).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  it("gives the owner the five actions, and no install sentence", async () => {
    mount(client(OWNED));

    await screen.findByRole("heading", { level: 1, name: /alice/ });
    const names = screen.getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual([
      word("skillHub.edit"),
      word("skillHub.unpublish"),
      word("skillHub.share"),
      word("skillHub.transfer"),
      word("skillHub.delete"),
    ]);
    expect(screen.queryByText(word("skillHub.howToInstall"))).toBeNull();
    expect(
      screen.getByText(word("skillHub.visibility.public")),
    ).toBeInTheDocument();
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
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.unpublish") }),
    );
    await waitFor(() => expect(c.unpublish).toHaveBeenCalledWith("e-1"));

    cleanup();
    const c2 = client(detail({ ...OWNED, visibility: "private" }));
    mount(c2);
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.republish") }),
    );
    await waitFor(() => expect(c2.republish).toHaveBeenCalledWith("e-1"));
    expect(
      screen.getByText(word("skillHub.visibility.private")),
    ).toBeInTheDocument();
  });

  it("deletes only after the confirm, then leaves for the list", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.delete") }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(
      word("skillHub.delete.title", { name: "triage-reflow" }),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: word("skillHub.cancel") }),
    );
    expect(c.remove).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: word("skillHub.delete") }),
    );
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: word("skillHub.delete.confirm"),
      }),
    );
    await waitFor(() => expect(c.remove).toHaveBeenCalledWith("e-1"));
    expect(await screen.findByText(/^LIST PAGE/)).toBeInTheDocument();
  });

  it("Edit opens the source item when the server says open", async () => {
    mount(client(OWNED));
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.edit") }),
    );

    expect(await screen.findByText("ITEM PAGE")).toBeInTheDocument();
  });

  it("Edit explains a closed source item and points at a new one", async () => {
    mount(
      client(OWNED, {
        action: "new_item",
        app: "rca",
        profile: "default",
        item_id: "",
        reason: "closed",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.edit") }),
    );

    const dialog = await screen.findByTestId("skill-hub-new-item");
    expect(dialog).toHaveTextContent(word("skillHub.edit.reason.closed"));
    // Review round 1: the profile the skill was written for rode along
    // nowhere; the new item opened on the App's default profile.
    expect(
      within(dialog).getByRole("link", {
        name: word("skillHub.edit.newItem.go"),
      }),
    ).toHaveAttribute("href", "/a/rca/new?profile=default&skill=e-1");
  });

  it("transfers to the person picked", async () => {
    const c = client(OWNED);
    mount(c);
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.transfer") }),
    );

    const dialog = await screen.findByTestId("skill-hub-transfer");
    const go = within(dialog).getByRole("button", {
      name: word("skillHub.transfer.confirm"),
    });
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
    mount(
      client(
        detail({
          forked_from: {
            entry: "e-root",
            state: "live",
            owner: "carol",
            name: "triage-reflow",
          },
        }),
      ),
    );
    expect(
      await screen.findByRole("link", {
        name: word("skillHub.forkOf", { origin: "carol/triage-reflow" }),
      }),
    ).toHaveAttribute("href", "/skill-hub/e-root");

    cleanup();
    mount(
      client(
        detail({
          forked_from: {
            entry: "e-root",
            state: "unpublished",
            owner: "",
            name: "",
          },
        }),
      ),
    );
    expect(
      await screen.findByText(word("skillHub.origin.unpublished")),
    ).toBeInTheDocument();

    cleanup();
    mount(
      client(
        detail({
          forked_from: {
            entry: "e-root",
            state: "deleted",
            owner: "",
            name: "",
          },
        }),
      ),
    );
    expect(
      await screen.findByText(word("skillHub.origin.deleted")),
    ).toBeInTheDocument();
  });

  it("shows a failed action's reason instead of swallowing it", async () => {
    const c = client(OWNED);
    // The shape the real client throws (`api/skillHub.test.ts`).
    c.unpublish.mockRejectedValueOnce(
      new HttpError(403, "only the owner may manage this entry"),
    );
    mount(c);
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.unpublish") }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "only the owner may manage this entry",
    );
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

  it("does not ALSO raise the global write-failure toast for a failure it shows itself (D3)", async () => {
    resetWriteFailures();
    const c = client(OWNED);
    c.unpublish.mockRejectedValueOnce(
      new HttpError(403, "only the owner may manage this entry"),
    );
    mount(c, makeQueryClient());
    fireEvent.click(
      await screen.findByRole("button", { name: word("skillHub.unpublish") }),
    );

    await screen.findByRole("alert");
    expect(currentWriteFailure()).toBeNull();
  });

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

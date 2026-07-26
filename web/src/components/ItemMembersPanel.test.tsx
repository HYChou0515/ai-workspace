// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { ItemMembersPanel } from "./ItemMembersPanel";

vi.mock("../api", () => ({
  api: { setItemPermission: vi.fn(), getCurrentUser: vi.fn(), getMe: vi.fn() },
}));
import { api } from "../api";

vi.mock("./UserChip", () => ({
  UserChip: ({ userId }: { userId: string }) => <span>{userId}</span>,
}));
const pickable = vi.hoisted(() => ({ groups: [] as Array<Record<string, unknown>> }));
vi.mock("../hooks/usePickableGroups", () => ({ usePickableGroups: () => pickable.groups }));
const mine = vi.hoisted(() => ({ groups: [] as Array<Record<string, unknown>> }));
vi.mock("../hooks/useMyGroups", () => ({ useMyGroups: () => mine.groups }));
vi.mock("./ItemShareDialog", () => ({
  ItemShareDialog: ({ value, onSubmit }: { value: { visibility: string }; onSubmit: (p: unknown) => void }) => (
    <div data-testid="share-dialog" data-visibility={value.visibility}>
      <button type="button" data-testid="stub-save" onClick={() => onSubmit({ visibility: "public" })}>
        save
      </button>
    </div>
  ),
}));

const manifest = { slug: "rca", labels: {}, item: { noun: "Investigation" } } as unknown as AppManifest;

/** alice owns it; bob is a Participant, carol only sees it exists. */
const item = {
  resource_id: "INC-1",
  title: "Reflow drift",
  owner: "alice",
  created_by: "alice",
  members: ["bob", "carol"],
  permission: {
    visibility: "restricted",
    read_meta: ["user:bob", "user:carol"],
    read_chat: ["user:bob"],
    read_content: ["user:bob"],
    converse: ["user:bob"],
  },
} as unknown as AppItem;

function render(override: Record<string, unknown> = {}, m: AppManifest = manifest) {
  return renderWithQuery(
    <ItemMembersPanel manifest={m} item={{ ...item, ...override } as AppItem} />,
  );
}

function signInAs(id: string, isSuperuser = false) {
  vi.mocked(api.getCurrentUser).mockResolvedValue(id);
  vi.mocked(api.getMe).mockResolvedValue({ id, is_superuser: isSuperuser, groups: [] });
}

beforeEach(() => {
  signInAs("alice");
  pickable.groups = [];
  mine.groups = [];
});
afterEach(cleanup);

describe("ItemMembersPanel", () => {
  it("titles itself from the manifest label, defaulting to Members", async () => {
    render();
    expect(await screen.findByTestId("members-title")).toHaveTextContent("Members");
  });

  // "App is a template": an App that calls them something else overrides the
  // `members` field label — and BOTH surfaces read that same label, so the top bar
  // and the sidebar can never drift apart again the way Members/Reviewers had.
  it("honours an App's own word for the roster", async () => {
    render({}, { ...manifest, labels: { members: "Reviewers" } } as unknown as AppManifest);
    expect(await screen.findByTestId("members-title")).toHaveTextContent("Reviewers");
  });

  it("lists the owner first, then each member with the role their grants give them", async () => {
    render();
    const rows = await screen.findAllByTestId(/^member-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "member-row-alice",
      "member-row-bob",
      "member-row-carol",
    ]);
    expect(screen.getByTestId("member-row-alice")).toHaveTextContent("Owner");
    expect(screen.getByTestId("member-row-bob")).toHaveTextContent("Participant");
    expect(screen.getByTestId("member-row-carol")).toHaveTextContent("Discoverable");
  });

  // A roster entry with no grants is a real, previously invisible state: the two
  // old panels listed `members` and said nothing about access, so someone could
  // sit on the roster with no way in and nobody could tell.
  it("flags a member who holds no grants at all", async () => {
    render({ members: ["dave"], permission: { visibility: "restricted" } });
    expect(await screen.findByTestId("member-row-dave")).toHaveTextContent("No access");
  });

  // Someone granted access but never added to the roster still has to appear —
  // otherwise the panel understates who can reach the item.
  it("includes a grantee who is not on the roster", async () => {
    render({ members: [], permission: { visibility: "restricted", read_chat: ["user:erin"] } });
    expect(await screen.findByTestId("member-row-erin")).toBeInTheDocument();
  });

  // A public item is reachable by everyone, so listing "you and him" as if only
  // those two have access is misleading — say "Everyone" instead, with the chip.
  it("shows a public item as Everyone, not a member list", async () => {
    render({ permission: { visibility: "public" }, members: ["bob"] });
    expect(await screen.findByText(/Everyone can access this/i)).toBeInTheDocument();
    expect(screen.getByText("Public")).toBeInTheDocument(); // AccessChip
    expect(screen.queryByTestId("member-row-bob")).not.toBeInTheDocument();
  });

  // A private item is owner-only; other roster names have no access, so showing
  // them is confusing. The owner sees "Only you." + the Private chip.
  it("shows a private item as Only you (owner), not other members", async () => {
    render({ permission: { visibility: "private" }, members: ["bob"] });
    expect(await screen.findByText(/Only you/i)).toBeInTheDocument();
    expect(screen.getByText("Private")).toBeInTheDocument();
    expect(screen.queryByTestId("member-row-bob")).not.toBeInTheDocument();
  });

  // #608 — a group granted access must appear in the roster (the panel used to
  // read only user grants), resolving the group's name like the share dialog.
  it("lists a granted group with its resolved name and role", async () => {
    pickable.groups = [{ resource_id: "group:g1", name: "Eng Team", description: "", member_count: 5 }];
    render({
      members: [],
      permission: { visibility: "restricted", read_meta: ["group:group:g1"], read_chat: ["group:group:g1"] },
    });
    const row = await screen.findByTestId("group-row-group:g1");
    expect(row).toHaveTextContent("Eng Team");
    expect(row).toHaveTextContent("In workspace");
  });

  // The group row shows a head-count; expanding it (collapsed by default) reveals
  // who is actually in the group, resolved from the caller's visible groups.
  it("expands a granted group to reveal its members, collapsed by default", async () => {
    pickable.groups = [{ resource_id: "group:g1", name: "Eng Team", description: "", member_count: 2 }];
    mine.groups = [
      { resource_id: "group:g1", name: "Eng Team", description: "", members: ["bob", "carol"], owner: "alice", maintainers: [] },
    ];
    render({ members: [], permission: { visibility: "restricted", read_meta: ["group:group:g1"], read_chat: ["group:group:g1"] } });
    // collapsed: members are not shown yet
    expect(screen.queryByTestId("group-member-group:g1-bob")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Eng Team/i }));
    expect(screen.getByTestId("group-member-group:g1-bob")).toBeInTheDocument();
    expect(screen.getByTestId("group-member-group:g1-carol")).toBeInTheDocument();
  });

  it("offers access management to someone who may change permission", async () => {
    render();
    fireEvent.click(await screen.findByTestId("members-manage"));
    expect(screen.getByTestId("share-dialog")).toBeInTheDocument();
  });

  it("prefills the dialog as Public when the item has NO permission (absent ≡ public)", async () => {
    // The backend treats an absent permission as public, and the row's
    // AccessChip says so. Prefilling the dialog "private" here meant an owner
    // who opened it and hit Save silently locked an item everyone could open —
    // the dialog and the chip contradicting each other (#587 family).
    render({ permission: undefined });
    fireEvent.click(await screen.findByTestId("members-manage"));
    expect(screen.getByTestId("share-dialog")).toHaveAttribute("data-visibility", "public");
  });

  it("refuses to edit a permission it cannot parse — no dialog, no guessed prefill", async () => {
    // #578's fail-closed rule: absent ≡ public, but present-and-UNPARSEABLE
    // (FE/BE version skew — say a fourth visibility literal) is NOT folded in.
    // The AccessChip already says "unknown" for such rows; opening the editor
    // with a guessed Public prefill would turn that guess into a PUT that also
    // wipes whatever grants the FE failed to parse.
    render({ permission: { visibility: "experimental" } });
    fireEvent.click(await screen.findByTestId("members-manage"));
    expect(screen.getByTestId("access-unreadable")).toBeInTheDocument();
    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();
  });

  it("saves through the permission endpoint and closes", async () => {
    vi.mocked(api.setItemPermission).mockResolvedValue({ visibility: "public", notified: [] });
    render();
    fireEvent.click(await screen.findByTestId("members-manage"));
    fireEvent.click(screen.getByTestId("stub-save"));

    await waitFor(() =>
      expect(api.setItemPermission).toHaveBeenCalledWith("rca", "INC-1", { visibility: "public" }),
    );
    await waitFor(() => expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument());
  });

  it("stays read-only for someone who may not change permission", async () => {
    signInAs("bob");
    render();
    await screen.findByTestId("member-row-alice");
    expect(screen.queryByTestId("members-manage")).not.toBeInTheDocument();
  });

  it("offers access management to a superuser who does not own the item", async () => {
    signInAs("root", true);
    render();
    expect(await screen.findByTestId("members-manage")).toBeInTheDocument();
  });

  // Inside a Popover the panel must NOT host the dialog: the popover is its own
  // z-index stacking context and closes on any outside mousedown, so a modal
  // owned in here would be z-capped and torn down by its own first click. The
  // caller takes the click and renders ItemAccessDialog above the popover.
  it("delegates the click upward when the host owns the dialog", async () => {
    const onManage = vi.fn();
    renderWithQuery(
      <ItemMembersPanel manifest={manifest} item={item} variant="popover" onManage={onManage} />,
    );
    fireEvent.click(await screen.findByTestId("members-manage"));
    expect(onManage).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("share-dialog")).not.toBeInTheDocument();
  });
});

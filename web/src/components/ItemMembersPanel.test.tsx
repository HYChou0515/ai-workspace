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
  vi.mocked(api.getMe).mockResolvedValue({ id, is_superuser: isSuperuser });
}

beforeEach(() => signInAs("alice"));
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

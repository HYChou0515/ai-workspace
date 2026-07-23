// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppItem } from "../api/types";
import { QueryWrap } from "../test/queryWrapper";
import { useItemAccess } from "./useItemAccess";

vi.mock("../api", () => ({ api: { getCurrentUser: vi.fn(), getMe: vi.fn() } }));
import { api } from "../api";

/** Someone else's private item — the case the admin could see but not open. */
const privateItem = {
  resource_id: "INC-1",
  created_by: "alice",
  permission: { visibility: "private" },
} as unknown as AppItem;

function signInAs(id: string, isSuperuser: boolean, groups: string[] = []) {
  vi.mocked(api.getCurrentUser).mockResolvedValue(id);
  vi.mocked(api.getMe).mockResolvedValue({ id, is_superuser: isSuperuser, groups });
}

function access(item: AppItem) {
  return renderHook(() => useItemAccess(item), {
    wrapper: ({ children }) => <QueryWrap>{children}</QueryWrap>,
  });
}

describe("useItemAccess", () => {
  // The bug: `work_item_access_scope` honours superusers, so an admin sees other
  // people's private items in the list — but every FE read gate stopped at
  // `visibility === "private"`, so the workspace rendered with no file tree and a
  // read-only composer, and no error explaining why. Identity has TWO parts and
  // the call sites only ever supplied one; this hook supplies both.
  it("opens the whole workspace to a superuser who does not own the item", async () => {
    signInAs("root", true);
    const { result } = access(privateItem);

    await waitFor(() => expect(result.current.canSeeFiles).toBe(true));
    expect(result.current.canReadChat).toBe(true);
    expect(result.current.canConverse).toBe(true);
    expect(result.current.canWrite).toBe(true);
    expect(result.current.isDiscoverableOnly).toBe(false);
  });

  // Deliberately NOT "everything false on a private item": every value there is
  // also the pre-resolution value, so such a test passes even if the hook never
  // reads identity at all. Wait for a DENIED verb to turn false — pre-settle the
  // hook is optimistic (all true), so a denial is proof identity resolved — and
  // only then assert the grant survived.
  it("still denies the verbs a plain non-owner was not granted", async () => {
    signInAs("dave", false);
    const { result } = access({
      ...privateItem,
      permission: { visibility: "restricted", read_meta: ["user:dave"], read_chat: ["user:dave"] },
    } as unknown as AppItem);

    await waitFor(() => expect(result.current.canSeeFiles).toBe(false));
    expect(result.current.canReadChat).toBe(true);
    expect(result.current.canConverse).toBe(false);
    expect(result.current.canWrite).toBe(false);
  });

  it("the owner keeps full access without being a superuser", async () => {
    signInAs("alice", false);
    const { result } = access(privateItem);

    await waitFor(() => expect(result.current.canSeeFiles).toBe(true));
    expect(result.current.canWrite).toBe(true);
  });

  it("read_meta without read_chat is still the 🔒 discoverable-only row", async () => {
    signInAs("bob", false);
    const { result } = access({
      ...privateItem,
      permission: { visibility: "restricted", read_meta: ["user:bob"] },
    } as unknown as AppItem);

    await waitFor(() => expect(result.current.isDiscoverableOnly).toBe(true));
    expect(result.current.canSeeFiles).toBe(false);
  });

  // #608: access granted VIA a group the caller belongs to must light up their
  // affordances — the server resolves the group grant, so the FE must too (else
  // the button is hidden on an item the user can actually edit).
  it("opens affordances for a member granted access through a group", async () => {
    signInAs("frank", false, ["eng"]); // frank is in group eng
    const { result } = access({
      ...privateItem,
      permission: {
        visibility: "restricted",
        read_meta: ["group:eng"],
        read_chat: ["group:eng"],
        read_content: ["group:eng"],
        edit_content: ["group:eng"],
      },
    } as unknown as AppItem);

    await waitFor(() => expect(result.current.canWrite).toBe(true));
    expect(result.current.canSeeFiles).toBe(true);
    expect(result.current.canReadChat).toBe(true);
    expect(result.current.isDiscoverableOnly).toBe(false);
  });

  it("denies a user NOT in the granted group", async () => {
    signInAs("grace", false, ["hr"]); // grace is in hr, not eng
    const { result } = access({
      ...privateItem,
      permission: { visibility: "restricted", read_meta: ["group:eng"], read_content: ["group:eng"] },
    } as unknown as AppItem);

    // grace has read_meta via... no — she's not in eng, so she sees nothing.
    await waitFor(() => expect(result.current.canSeeFiles).toBe(false));
    expect(result.current.canReadChat).toBe(false);
  });

  // The identity half of the LOADING CONTRACT: before `GET current user` / `GET
  // /me` resolve, useCurrentUser says "default-user" and useIsSuperuser says
  // false — which read as "a nobody". On a cold deep-link that locked the very
  // people the item belongs to (owner, admin) out of the first paint: 🔒 rows,
  // a vanished IDE column — and useEntityWrite silently DROPPED a write
  // completed inside the window. Identity-pending must stay optimistic, same
  // direction the contract already chose for a still-loading item.
  it("stays optimistic while identity is still resolving (no pessimistic lock flash)", () => {
    vi.mocked(api.getCurrentUser).mockReturnValue(new Promise(() => {}));
    vi.mocked(api.getMe).mockReturnValue(new Promise(() => {}));
    const { result } = access(privateItem);

    expect(result.current.canReadChat).toBe(true);
    expect(result.current.canSeeFiles).toBe(true);
    expect(result.current.canConverse).toBe(true);
    expect(result.current.canWrite).toBe(true);
    expect(result.current.isDiscoverableOnly).toBe(false);
  });
});

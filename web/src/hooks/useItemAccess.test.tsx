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

function signInAs(id: string, isSuperuser: boolean) {
  vi.mocked(api.getCurrentUser).mockResolvedValue(id);
  vi.mocked(api.getMe).mockResolvedValue({ id, is_superuser: isSuperuser });
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
  // reads identity at all. Grant one verb, wait for THAT to turn true — proof the
  // queries resolved — and only then assert the others are still denied.
  it("still denies the verbs a plain non-owner was not granted", async () => {
    signInAs("dave", false);
    const { result } = access({
      ...privateItem,
      permission: { visibility: "restricted", read_meta: ["user:dave"], read_chat: ["user:dave"] },
    } as unknown as AppItem);

    await waitFor(() => expect(result.current.canReadChat).toBe(true));
    expect(result.current.canSeeFiles).toBe(false);
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
});

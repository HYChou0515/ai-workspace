// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem } from "../api/types";
import { ShareChatDialog } from "./ShareChatDialog";

const setPermissionAsync = vi.fn(() => Promise.resolve());
vi.mock("../hooks/useResources", () => ({
  useSetItemPermission: () => ({ setPermissionAsync, isPending: false, error: null }),
}));
vi.mock("../hooks/usePickableGroups", () => ({
  usePickableGroups: () => [{ resource_id: "g1", name: "Ops team", description: "", member_count: 3 }],
}));
vi.mock("./UserPicker", () => ({ UserPicker: () => <div data-testid="userpicker" /> }));

afterEach(() => {
  cleanup();
  setPermissionAsync.mockClear();
});

const item = { resource_id: "playground-item:1", title: "Trip", owner: "me" } as unknown as AppItem;

describe("ShareChatDialog", () => {
  it("grants read + converse to the chosen group (restricted), via the permission endpoint", () => {
    render(<ShareChatDialog slug="playground" item={item} onClose={() => {}} />);
    fireEvent.click(screen.getByLabelText("Ops team")); // tick the group
    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    expect(setPermissionAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        visibility: "restricted",
        read_meta: ["group:g1"],
        read_chat: ["group:g1"],
        read_content: ["group:g1"],
        converse: ["group:g1"],
      }),
    );
  });

  it("falls back to private when nothing is selected (unshare)", () => {
    render(<ShareChatDialog slug="playground" item={item} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    expect(setPermissionAsync).toHaveBeenCalledWith(
      expect.objectContaining({ visibility: "private", read_meta: [] }),
    );
  });
});

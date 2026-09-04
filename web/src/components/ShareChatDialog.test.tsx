// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AppItem } from "../api/types";
import { DialogProvider } from "./Dialog";
import { ShareChatDialog } from "./ShareChatDialog";

// The confirm dialog is at the app root (#779) and this asks through it before
// dropping an unsent share.
const render = (ui: Parameters<typeof rtlRender>[0]) =>
  rtlRender(ui, { wrapper: DialogProvider });

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

  // #779: same quiet failure as the other share surfaces — close and the people
  // you picked were simply never granted anything, with nothing said.
  it("asks before dropping an unsent share, and keeps the picks", async () => {
    const onClose = vi.fn();
    render(<ShareChatDialog slug="playground" item={item} onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("Ops team"));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByLabelText("Ops team")).toBeChecked();
    expect(setPermissionAsync).not.toHaveBeenCalled();
  });

  it("closes without asking when nothing was picked", () => {
    const onClose = vi.fn();
    render(<ShareChatDialog slug="playground" item={item} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
  });
});

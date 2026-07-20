// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { EditItemModal } from "./WorkspaceShell";

vi.mock("../../api", () => ({
  api: { setItemPermission: vi.fn(), getCurrentUser: vi.fn(), getMe: vi.fn() },
}));
import { api } from "../../api";

// The dialog's own behaviour is covered by ItemShareDialog.test.tsx; stub it down
// to a Save button so these tests are about the ENTRY POINT and the save wiring.
vi.mock("../../components/ItemShareDialog", () => ({
  ItemShareDialog: ({
    busy,
    error,
    onSubmit,
  }: {
    busy?: boolean;
    error?: string | null;
    onSubmit: (p: unknown) => void;
  }) => (
    <div>
      <button
        type="button"
        data-testid="stub-save"
        disabled={busy}
        onClick={() => onSubmit({ visibility: "private" })}
      >
        save
      </button>
      {error ? <div data-testid="stub-error">{error}</div> : null}
    </div>
  ),
}));
// The schema-driven form is irrelevant here (and needs a full manifest).
vi.mock("../../components/ItemForm", () => ({
  ItemForm: () => <form />,
  pruneEmpty: (v: Record<string, unknown>) => v,
}));

const manifest = { slug: "rca", item: { noun: "Investigation" } } as unknown as AppManifest;

const item = {
  resource_id: "INC-1",
  title: "Reflow drift",
  owner: "alice",
  created_by: "alice",
  permission: { visibility: "private" },
} as unknown as AppItem;

function open() {
  return renderWithQuery(
    <EditItemModal manifest={manifest} item={item} onClose={vi.fn()} onSubmit={vi.fn()} />,
  );
}

beforeEach(() => {
  vi.mocked(api.getCurrentUser).mockResolvedValue("alice");
  vi.mocked(api.getMe).mockResolvedValue({ id: "alice", is_superuser: false });
});
afterEach(cleanup);

describe("EditItemModal — access management", () => {
  it("saves through the dedicated permission endpoint and closes the dialog", async () => {
    vi.mocked(api.setItemPermission).mockResolvedValue({ visibility: "private", notified: [] });
    open();

    fireEvent.click(await screen.findByTestId("manage-access"));
    fireEvent.click(screen.getByTestId("stub-save"));

    await waitFor(() =>
      expect(api.setItemPermission).toHaveBeenCalledWith("rca", "INC-1", { visibility: "private" }),
    );
    await waitFor(() => expect(screen.queryByTestId("stub-save")).not.toBeInTheDocument());
  });

  // The old wiring `await`ed the call inside a `() => void` prop, so a 403 became
  // an unhandled rejection: the dialog neither closed nor said why.
  it("surfaces a failure and keeps the dialog open", async () => {
    vi.mocked(api.setItemPermission).mockRejectedValue(new Error("not authorized to change_permission"));
    open();

    fireEvent.click(await screen.findByTestId("manage-access"));
    fireEvent.click(screen.getByTestId("stub-save"));

    expect(await screen.findByTestId("stub-error")).toHaveTextContent("not authorized");
    expect(screen.getByTestId("stub-save")).toBeInTheDocument();
  });
});

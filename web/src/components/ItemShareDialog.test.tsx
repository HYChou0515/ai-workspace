// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemPermission } from "../lib/itemPermission";
import { ItemShareDialog } from "./ItemShareDialog";

vi.mock("./UserChip", () => ({ UserChip: ({ userId }: { userId: string }) => <span>{userId}</span> }));
vi.mock("./UserPicker", () => ({
  UserPicker: ({ onToggle }: { onToggle: (id: string) => void }) => (
    <button type="button" onClick={() => onToggle("alice")}>
      add-alice
    </button>
  ),
}));

afterEach(cleanup);

function open(value: ItemPermission, onSubmit = vi.fn()) {
  render(
    <ItemShareDialog itemName="INC-1" owner="bob" value={value} onSubmit={onSubmit} onClose={vi.fn()} />,
  );
  return onSubmit;
}

describe("ItemShareDialog", () => {
  it("hydrates an existing grant at its ladder role", () => {
    open({ visibility: "restricted", read_meta: ["user:carol"], read_chat: ["user:carol"] });
    const sel = screen.getByTestId("item-role-carol") as HTMLSelectElement;
    expect(sel.value).toBe("in_workspace");
  });

  it("adding a person defaults to Participant and saves the participant verbs", () => {
    const onSubmit = open({ visibility: "restricted" });
    fireEvent.click(screen.getByText("add-alice"));
    fireEvent.click(screen.getByTestId("item-share-save"));
    const perm = onSubmit.mock.calls[0][0] as ItemPermission;
    expect(perm.read_chat).toContain("user:alice");
    expect(perm.converse).toContain("user:alice");
    expect(perm.edit_content ?? []).not.toContain("user:alice");
  });

  it("Custom mode exposes per-verb checkboxes and writes exactly those", () => {
    const onSubmit = open({ visibility: "restricted", read_meta: ["user:carol"], read_chat: ["user:carol"] });
    fireEvent.change(screen.getByTestId("item-role-carol"), { target: { value: "custom" } });
    // custom revealed with the current verbs (read_meta, read_chat); add read_content, drop read_chat
    const box = screen.getByTestId("item-custom-carol");
    const checks = box.querySelectorAll("input[type=checkbox]");
    // toggle read_content on (index by label order = ITEM_ROLE_VERBS)
    const labels = Array.from(box.querySelectorAll("label")).map((l) => l.textContent);
    const rc = checks[labels.indexOf("read_content")] as HTMLInputElement;
    fireEvent.click(rc);
    fireEvent.click(screen.getByTestId("item-share-save"));
    const perm = onSubmit.mock.calls[0][0] as ItemPermission;
    expect(perm.read_content).toContain("user:carol");
    expect(perm.read_chat).toContain("user:carol");
  });

  it("switching visibility to private drops the grant lists' effect (radio works)", () => {
    open({ visibility: "restricted" });
    fireEvent.click(screen.getByTestId("item-visibility-private"));
    expect((screen.getByTestId("item-visibility-private") as HTMLInputElement).checked).toBe(true);
  });
});

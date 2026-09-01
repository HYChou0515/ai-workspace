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

describe("ItemShareDialog — group grants (#608)", () => {
  const pickable = [{ resource_id: "eng", name: "Engineering", description: "", member_count: 12 }];
  const openG = (value: ItemPermission, onSubmit = vi.fn()) => {
    render(
      <ItemShareDialog
        itemName="INC-1"
        owner="bob"
        value={value}
        pickableGroups={pickable}
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    return onSubmit;
  };

  it("shows an existing group grant by name and keeps it on save", () => {
    const onSubmit = openG({
      visibility: "restricted",
      read_meta: ["group:eng"],
      read_chat: ["group:eng"],
    });
    expect(screen.getByText("Engineering")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("item-share-save"));
    const saved = onSubmit.mock.calls[0][0] as ItemPermission;
    expect(saved.read_chat).toContain("group:eng");
  });

  it("keeps groups behind their own tab instead of stacking them under the people list", () => {
    openG({ visibility: "restricted", read_meta: ["user:carol"], read_chat: ["user:carol"] });
    // People first: the group picker is not competing for the panel's height.
    expect(screen.queryByTestId("item-group-select")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("share-tab-groups"));
    expect(screen.getByTestId("item-group-select")).toBeInTheDocument();
    expect(screen.queryByTestId("item-people-picker")).not.toBeInTheDocument();
  });

  it("opens on the side that already has grants, so an existing group grant is not hidden", () => {
    openG({ visibility: "restricted", read_meta: ["group:eng"], read_chat: ["group:eng"] });
    expect(screen.getByTestId("share-tab-groups")).toHaveAttribute("aria-selected", "true");
  });

  it("adds a group grant from the picker", () => {
    const onSubmit = openG({ visibility: "restricted" });
    fireEvent.click(screen.getByTestId("share-tab-groups"));
    fireEvent.click(screen.getByTestId("group-picker-item-eng"));
    fireEvent.click(screen.getByTestId("item-share-save"));
    const saved = onSubmit.mock.calls[0][0] as ItemPermission;
    expect(saved.read_meta).toContain("group:eng");
  });
});

/* The share modal's panel is a flex column with a max-height, so anything the
 * flex layout is allowed to compress gets compressed INSTEAD of the panel
 * growing a scrollbar. That is how six granted people used to squeeze the
 * "Add people…" picker (a scroll container, minimum size 0) down to a 13px
 * sliver with no way to scroll it back: the only cure was removing someone. */
describe("<ItemShareDialog /> layout — a long grant list must not eat the picker", () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) => `user:p${i}`);

  it("refuses to let the flex column compress the people section", () => {
    open({ visibility: "restricted", read_meta: many(6), read_chat: many(6) });
    expect(screen.getByTestId("item-share-grants").style.flexShrink).toBe("0");
  });

  it("does not wrap the picker in a second scroll layer that hides its search box", () => {
    open({ visibility: "restricted", read_meta: many(6), read_chat: many(6) });
    // UserPicker already caps and scrolls its own result list. A scrollable box
    // around it scrolls the SEARCH INPUT out of sight the moment you click
    // someone far down the list — the picker is there, and unusable.
    expect(screen.getByTestId("item-people-picker").style.overflow).toBe("");
  });

  it("keeps Save in the pinned action bar so eight grants cannot scroll it away", () => {
    open({ visibility: "restricted", read_meta: many(8), read_chat: many(8) });
    const save = screen.getByTestId("item-share-save");
    expect(save.closest('[data-testid="modal-actions"]')).not.toBeNull();
  });

  it("scrolls a long grant list in its own box instead of pushing the picker away", () => {
    open({ visibility: "restricted", read_meta: many(6), read_chat: many(6) });
    const list = screen.getByTestId("item-grant-list");
    expect(list.style.overflow).toBe("auto");
    expect(list.style.maxHeight).not.toBe("");
  });
});

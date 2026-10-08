// @vitest-environment happy-dom
/**
 * The menu button (WAI-ARIA APG "Menu Button"): secondary and destructive
 * actions behind one labelled trigger (plan-skill-hub-ux-redo D5, D9).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionMenu } from "./ActionMenu";
import { ModalShell } from "./ModalShell";

afterEach(cleanup);

function setup(disabled = false) {
  const edit = vi.fn();
  const remove = vi.fn();
  render(
    <ActionMenu
      label="管理"
      items={[
        { id: "edit", label: "修改", onSelect: edit },
        { id: "off", label: "不能按", onSelect: vi.fn(), disabled: true },
        { id: "delete", label: "刪除", onSelect: remove, danger: true },
      ]}
      disabled={disabled}
    />,
  );
  return { edit, remove, trigger: screen.getByRole("button", { name: "管理" }) };
}

describe("ActionMenu", () => {
  it("opens on the trigger, focuses the first item, and runs the one picked", () => {
    const { edit, trigger } = setup();
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const items = screen.getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual(["修改", "不能按", "刪除"]);
    expect(document.activeElement).toBe(items[0]);
    expect(items[1]).toBeDisabled();
    expect(items[2]).toHaveAttribute("data-variant", "danger");

    fireEvent.click(items[0]);
    expect(edit).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("moves with the arrow keys past a disabled item, and Escape closes back to the trigger", () => {
    const { trigger } = setup();
    fireEvent.click(trigger);
    const menu = screen.getByRole("menu");
    const [first, , last] = screen.getAllByRole("menuitem");

    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(menu, { key: "ArrowUp" });
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("Escape closes the menu, not the modal it sits in (the Skills panel's ⋯)", () => {
    const close = vi.fn();
    render(
      <ModalShell onClose={close} ariaLabel="panel">
        <ActionMenu label="更多" items={[{ id: "a", label: "下載", onSelect: vi.fn() }]} />
      </ModalShell>,
    );
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(close).not.toHaveBeenCalled();
  });

  it("closes when focus leaves it — Tab out of the menu (APG)", () => {
    render(
      <>
        <ActionMenu label="更多" items={[{ id: "a", label: "下載", onSelect: vi.fn() }]} />
        <button type="button">next</button>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    const item = screen.getByRole("menuitem");
    fireEvent.focusOut(item, { relatedTarget: screen.getByRole("button", { name: "next" }) });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a blur to nowhere — a mouse press on an item in Safari, which does not focus a button — keeps it open", () => {
    const edit = vi.fn();
    render(<ActionMenu label="更多" items={[{ id: "a", label: "下載", onSelect: edit }]} />);
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    const item = screen.getByRole("menuitem");
    fireEvent.focusOut(item, { relatedTarget: null });
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem"));
    expect(edit).toHaveBeenCalledTimes(1);
  });

  it("Escape on the trigger of an open menu — every item disabled — closes the menu, not the modal", () => {
    const close = vi.fn();
    render(
      <ModalShell onClose={close} ariaLabel="panel">
        <ActionMenu label="更多" items={[{ id: "a", label: "下載", onSelect: vi.fn(), disabled: true }]} />
      </ModalShell>,
    );
    const trigger = screen.getByRole("button", { name: "更多" });
    fireEvent.click(trigger);
    expect(document.activeElement).not.toBe(screen.getByRole("menuitem"));
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(close).not.toHaveBeenCalled();
  });

  it("closes on a click outside", () => {
    const { trigger } = setup();
    fireEvent.click(trigger);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a disabled trigger opens nothing", () => {
    const { trigger } = setup(true);
    expect(trigger).toBeDisabled();
    fireEvent.click(trigger);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

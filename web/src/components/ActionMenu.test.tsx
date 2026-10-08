// @vitest-environment happy-dom
/**
 * The menu button (WAI-ARIA APG "Menu Button"): secondary and destructive
 * actions behind one labelled trigger (plan-skill-hub-ux-redo D5, D9).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionMenu } from "./ActionMenu";

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

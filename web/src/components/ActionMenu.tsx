/**
 * A menu button (WAI-ARIA APG "Menu Button", Material 3's overflow menu): the
 * actions a screen keeps out of its row of buttons — the secondary and the
 * destructive ones — behind one labelled trigger (plan-skill-hub-ux-redo D5,
 * D9). Click or Enter opens it with the first item focused; ↑/↓ move, skipping
 * disabled items; Escape and a click outside close it, Escape returning
 * focus to the trigger.
 */

import { useEffect, useId, useRef, useState } from "react";

import { Icon } from "./Icon";

export type ActionMenuItem = {
  id: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** A destructive action: drawn in the danger tone (and placed last by the caller). */
  danger?: boolean;
  testId?: string;
};

export function ActionMenu({
  label,
  items,
  disabled = false,
  iconOnly = false,
  align = "end",
  testId,
}: {
  /** The trigger's name — shown, or only its accessible name when `iconOnly`. */
  label: string;
  items: ActionMenuItem[];
  disabled?: boolean;
  /** A ⋯ trigger, for a row with no room for a word. */
  iconOnly?: boolean;
  align?: "start" | "end";
  /** The trigger's `data-testid`. */
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  const enabled = () =>
    Array.from(menu.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ?? []).filter(
      (b) => !b.disabled,
    );

  useEffect(() => {
    if (!open) return;
    enabled()[0]?.focus();
    const outside = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      // The menu's Escape, not the modal's around it: a modal listens on the
      // document, and without this one press closed the Skills panel too.
      e.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const list = enabled();
    const at = list.indexOf(document.activeElement as HTMLButtonElement);
    const step = e.key === "ArrowDown" ? 1 : -1;
    list[(at + step + list.length) % list.length]?.focus();
  };

  return (
    <div className="action-menu" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="btn"
        data-size="sm"
        data-variant={iconOnly ? "ghost" : "secondary"}
        data-testid={testId}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={iconOnly ? label : undefined}
        title={iconOnly ? label : undefined}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        {iconOnly ? (
          <Icon name="dots_h" size={14} />
        ) : (
          <>
            {label}
            <Icon name="chev_d" size={12} />
          </>
        )}
      </button>
      {open ? (
        <div
          id={id}
          ref={menu}
          role="menu"
          aria-label={label}
          className="action-menu__menu"
          data-align={align}
          onKeyDown={onKeyDown}
        >
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              data-testid={item.testId}
              className="action-menu__item"
              data-variant={item.danger ? "danger" : undefined}
              disabled={item.disabled}
              tabIndex={-1}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

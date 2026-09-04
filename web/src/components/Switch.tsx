/**
 * A setting that takes effect when you flip it.
 *
 * Not a checkbox: a checkbox is part of a form you submit, so it reads as a
 * choice that has not happened yet. A switch says the change is already made —
 * which is what a stored preference is.
 *
 * Presentation lives in the shared `.switch` class (styles/base.css), the same
 * arrangement `Btn` uses, so it themes with the App's tokens and gets real
 * :focus-visible and :disabled states that inline styles cannot express.
 *
 * `role="switch"` on a checkbox input is the ARIA-approved pairing, and it
 * keeps everything the browser already does: the label toggles it, Space works,
 * and the tab order is right.
 */
import type { ReactNode } from "react";

export function Switch({
  checked,
  onChange,
  children,
  title,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (on: boolean) => void;
  /** What it says on screen. Keep it short — the sentence goes in `label`. */
  children?: ReactNode;
  /** The whole sentence, on hover. */
  title?: string;
  /** The accessible name. Defaults to `title` so a mouse and a screen reader
   * are told the same thing; a short visible word alone rarely explains it. */
  label?: string;
  disabled?: boolean;
}) {
  return (
    <label className="switch" title={title} data-disabled={disabled ? "" : undefined}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        aria-label={label ?? title}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="switch-track" aria-hidden="true">
        <span className="switch-thumb" />
      </span>
      {children != null && <span className="switch-label">{children}</span>}
    </label>
  );
}

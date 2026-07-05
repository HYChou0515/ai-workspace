/**
 * Shared button matching the design-handoff `Btn` (system.jsx): variants
 * primary | secondary | ghost, sizes sm | md. Presentation lives in the shared
 * `.btn` class (styles/base.css) — driven by data-variant / data-size — so it
 * gets real :hover and :disabled states that inline styles can't express
 * (#445 #16). Tokens only, so it themes with the App's `--accent`. Hand-rolled
 * buttons can adopt the same look with class="btn" data-variant/-size.
 */
import type { CSSProperties, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

export function Btn({
  children,
  variant = "secondary",
  size = "md",
  icon,
  iconRight,
  fullWidth,
  active,
  disabled,
  title,
  onClick,
  style,
}: {
  children?: ReactNode;
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconRight?: ReactNode;
  fullWidth?: boolean;
  active?: boolean;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
  style?: CSSProperties;
}) {
  return (
    <button
      type="button"
      className="btn"
      data-variant={variant}
      data-size={size}
      data-active={active ? "" : undefined}
      data-fullwidth={fullWidth ? "" : undefined}
      disabled={disabled}
      onClick={onClick}
      title={title}
      style={style}
    >
      {icon && <span style={{ display: "inline-flex" }}>{icon}</span>}
      {children}
      {iconRight && <span style={{ display: "inline-flex", opacity: 0.6 }}>{iconRight}</span>}
    </button>
  );
}

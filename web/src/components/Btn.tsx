/**
 * Shared button matching the design-handoff `Btn` (system.jsx): variants
 * primary | secondary | ghost, sizes sm | md. Tokens only (no hardcoded
 * colors) so it themes with the App's `--accent`. Surfaces inline their own
 * one-offs historically; new design-aligned surfaces use this.
 */
import type { CSSProperties, ReactNode } from "react";
import { pxToRem } from "../lib/pxToRem";

type Variant = "primary" | "secondary" | "ghost";
type Size = "sm" | "md";

const SIZES: Record<Size, { height: number; padding: string; fontSize: string; gap: number }> = {
  sm: { height: 28, padding: "0 10px", fontSize: pxToRem(12), gap: 6 },
  md: { height: 36, padding: "0 14px", fontSize: pxToRem(13), gap: 8 },
};

const VARIANTS: Record<Variant, CSSProperties> = {
  primary: { background: "var(--accent)", color: "var(--white)", border: "1px solid transparent" },
  secondary: { background: "transparent", color: "var(--text-paper)", border: "1px solid var(--paper-3)" },
  ghost: { background: "transparent", color: "var(--text-paper-d)", border: "1px solid transparent" },
};

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
  const s = SIZES[size];
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      data-active={active ? "" : undefined}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: s.gap,
        height: s.height,
        padding: s.padding,
        fontFamily: "inherit",
        fontSize: s.fontSize,
        fontWeight: 500,
        borderRadius: "var(--radius-btn)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
        width: fullWidth ? "100%" : "auto",
        whiteSpace: "nowrap",
        transition: "background .15s, color .15s, border-color .15s",
        ...VARIANTS[variant],
        ...(active ? { background: "var(--accent-soft)", color: "var(--accent)" } : null),
        ...style,
      }}
    >
      {icon && <span style={{ display: "inline-flex" }}>{icon}</span>}
      {children}
      {iconRight && <span style={{ display: "inline-flex", opacity: 0.6 }}>{iconRight}</span>}
    </button>
  );
}

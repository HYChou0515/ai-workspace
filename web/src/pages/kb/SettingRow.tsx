/**
 * The collection settings panel's row language — an icon tile, a title, a
 * description, and a switch on the right.
 *
 * Lifted out of RetrievalToggles so every setting in the panel looks like the
 * same thing. It previously had two: these cards for retrieval, and bare
 * checkboxes for everything added later, which read as unfinished sitting under
 * them. Shared rather than copied — a duplicated card is a card that drifts.
 */

import type { ReactNode } from "react";

import { Icon, type IconName } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

export function Switch({
  on,
  onClick,
  label,
  disabled,
  testId,
}: {
  on: boolean;
  onClick: () => void;
  label: string;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      onClick={onClick}
      style={{
        width: 40,
        height: 22,
        borderRadius: 11,
        border: 0,
        padding: 0,
        background: on ? "var(--accent)" : "var(--paper-3)",
        position: "relative",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.6 : 1,
        flexShrink: 0,
        transition: "background .15s",
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 2,
          left: on ? 20 : 2,
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "var(--white)",
          transition: "left .15s",
          boxShadow: "0 1px 2px rgba(0,0,0,.15)",
        }}
      />
    </button>
  );
}

export function SettingRow({
  icon,
  title,
  desc,
  on,
  recommended,
  disabled,
  onToggle,
  testId,
  children,
}: {
  icon: IconName;
  title: string;
  desc: string;
  on: boolean;
  recommended?: boolean;
  disabled?: boolean;
  onToggle: () => void;
  testId?: string;
  /** Optional controls that belong to this setting (e.g. a "run it now" button),
   *  rendered under the description so they read as part of the same row. */
  children?: ReactNode;
}) {
  const t = useT();
  return (
    <div
      style={{
        display: "flex",
        gap: 12,
        padding: 14,
        background: "var(--white)",
        border: "1px solid var(--paper-3)",
        borderRadius: 8,
        alignItems: "flex-start",
      }}
    >
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: "var(--radius-btn)",
          background: on ? "var(--accent-soft)" : "var(--paper-2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <Icon name={icon} size={16} color={on ? "var(--accent-h)" : "var(--text-paper-d)"} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
          <span style={{ fontSize: pxToRem(14), fontWeight: 600, color: "var(--ink)" }}>{title}</span>
          {recommended && (
            <span
              style={{
                fontSize: pxToRem(10),
                fontWeight: 600,
                color: "var(--ok)",
                background: "color-mix(in srgb, var(--ok) 14%, transparent)",
                padding: "1px 6px",
                borderRadius: 4,
              }}
            >
              {t("kb.retrieval.recommended")}
            </span>
          )}
        </div>
        <div style={{ fontSize: pxToRem(12.5), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
          {desc}
        </div>
        {children}
      </div>
      <Switch on={on} onClick={onToggle} label={title} disabled={disabled} testId={testId} />
    </div>
  );
}

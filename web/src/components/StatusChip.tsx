/**
 * Severity (P0-P4) and Status (triaging/awaiting_review/resolved/abandoned)
 * chips, shared across Home table, breadcrumb, report header, etc.
 *
 * Tone mapping is the design's canonical color story for these enums.
 * Components in other modules should never re-implement the mapping —
 * import {severityTone, statusTone} or just use the chip.
 */

import type { ChipTone, Severity, Status } from "../api/types";
import { pxToRem } from "../lib/pxToRem";

// ChipTone now lives in api/types (shared with App `field_styles`); re-exported
// here so existing importers (DiagnosticsPage, DomainField) keep working.
export type { ChipTone };

export function severityTone(level: Severity): ChipTone {
  switch (level) {
    case "P0":
    case "P1":
      return "err";
    case "P2":
      return "warn";
    case "P3":
    case "P4":
      return "ok";
  }
}

export function statusTone(status: Status): ChipTone {
  switch (status) {
    case "triaging":
      return "warn";
    case "awaiting_review":
      return "info";
    case "resolved":
      return "ok";
    case "abandoned":
      return "muted";
  }
}

const TONE_BG: Record<ChipTone, string> = {
  err: "rgba(196,74,58,.12)",
  warn: "rgba(198,138,46,.14)",
  ok: "rgba(58,138,74,.12)",
  info: "rgba(45,108,201,.12)",
  muted: "var(--paper-2)",
};

const TONE_FG: Record<ChipTone, string> = {
  err: "var(--err)",
  warn: "var(--warn)",
  ok: "var(--ok)",
  info: "var(--info)",
  muted: "var(--text-paper-d)",
};

export function chipStyle(tone: ChipTone): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    padding: "2px 8px",
    borderRadius: "var(--radius-chip)",
    background: TONE_BG[tone],
    color: TONE_FG[tone],
    fontFamily: "var(--font-mono)",
    fontSize: pxToRem(11),
    fontWeight: 500,
    letterSpacing: "0.02em",
    whiteSpace: "nowrap",
  };
}

function Dot({ tone }: { tone: ChipTone }) {
  return (
    <span
      data-role="dot"
      style={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: TONE_FG[tone],
        opacity: 0.85,
      }}
    />
  );
}

export function SeverityChip({ level }: { level: Severity }) {
  const tone = severityTone(level);
  return (
    <span data-tone={tone} style={chipStyle(tone)}>
      {level}
    </span>
  );
}

const STATUS_LABEL: Record<Status, string> = {
  triaging: "triaging",
  awaiting_review: "awaiting review",
  resolved: "resolved",
  abandoned: "abandoned",
};

export function StatusChip({ status }: { status: Status }) {
  const tone = statusTone(status);
  return (
    <span data-tone={tone} style={chipStyle(tone)}>
      <Dot tone={tone} />
      {STATUS_LABEL[status]}
    </span>
  );
}

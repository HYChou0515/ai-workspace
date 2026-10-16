/**
 * How the capacity dashboard writes its numbers and names its lines
 * (docs/plan-capacity-usage.md §10). Copy says what a number is, never how it
 * was computed.
 */
import { isMsgKey, type MsgKey, type Vars } from "../lib/i18n";

import type { Line, Unit } from "./api";

export type T = (key: MsgKey, vars?: Vars) => string;

const GiB = 1024 ** 3;

function round(v: number, digits: number): string {
  if (v === 0) return "0";
  const abs = Math.abs(v);
  const d = abs >= 100 ? 0 : abs >= 10 ? Math.min(1, digits) : digits;
  return v.toLocaleString(undefined, { maximumFractionDigits: d });
}

export function bytes(v: number): string {
  const units: [number, string][] = [
    [1024 ** 4, "TiB"],
    [GiB, "GiB"],
    [1024 ** 2, "MiB"],
    [1024, "KiB"],
  ];
  for (const [size, name] of units) {
    if (Math.abs(v) >= size) return `${round(v / size, 1)} ${name}`;
  }
  return `${Math.round(v)} B`;
}

export function formatValue(t: T, value: number | null | undefined, unit: Unit): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (unit === "bytes") return bytes(value);
  if (unit === "cores") return t("capacity.unit.cores", { v: round(value, 2) });
  return t("capacity.unit.inflight", { v: round(value, 2) });
}

/** Machines: a whole count as it is; a share of shared machines (a worker
 * job type's part of the worker pool) to one decimal. */
export function machineCount(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return n < 0.05 ? "<0.1" : (Math.round(n * 10) / 10).toFixed(1);
}

export function pct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "—";
  return `${(x * 100).toFixed(digits)}%`;
}

function named(t: T, prefix: string, key: string): string {
  const id = `${prefix}.${key}`;
  return isMsgKey(id) ? t(id) : key;
}

/** Items in the language's list form ("a、b" / "a, b"). */
export function list(t: T, items: string[]): string {
  return items.join(t("capacity.sep.list"));
}

/** "API CPU", "沙盒 記憶體", "GPU qwen3", "背景工作（文件索引） CPU". */
export function lineLabel(
  t: T,
  line: Pick<Line, "component" | "resource" | "model"> & { job?: string },
): string {
  let component = named(t, "capacity.component", line.component);
  if (line.job) component = t("capacity.component.withJob", { component, job: purposeLabel(t, line.job) });
  if (line.component === "gpu") {
    const base = line.model ? `${component} ${line.model}` : component;
    return line.resource === "memory" ? `${base} ${named(t, "capacity.resource", "memory")}` : base;
  }
  return `${component} ${named(t, "capacity.resource", line.resource)}`;
}

export function lineLabelById(t: T, lines: Line[], id: string | null | undefined): string {
  const line = lines.find((l) => l.id === id);
  return line ? lineLabel(t, line) : (id ?? "");
}

/** A purpose key ("requests", "chat", a job type) in words where we have them. */
export function purposeLabel(t: T, key: string): string {
  return named(t, "capacity.purpose", key);
}

/** The rows that are not a person. */
export const NOT_PEOPLE = new Set(["unknown", "system", ""]);

export function personLabel(t: T, key: string, name: (id: string) => string): string {
  if (key === "unknown") return t("capacity.row.unknown");
  if (key === "system") return t("capacity.row.system");
  return name(key);
}

/** A local day ("2026-10-15") from the period's day list, as the browser writes dates. */
export function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "numeric", day: "numeric" });
}

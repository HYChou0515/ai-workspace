/**
 * What the page tells us went wrong, and how that reaches the agent.
 *
 * The whole point is that a domain expert can get unstuck without opening a
 * console or learning to describe a bug. So a report has two audiences and both
 * read the same text: it is shown in the pane in plain language, and it is
 * handed to the agent verbatim. Anything that only one of them could act on is
 * the wrong thing to record.
 */

import { WUI_PROTOCOL } from "./protocol";

export type WuiReportKind = "error" | "refused" | "pick";

/** How much markup a pick may carry into the chat box. */
export const MAX_PICK_HTML = 4000;

/** What the user pointed at. No screenshot (the CSP will not let us fetch a
 * canvas library) and no source map (the markup is generated, and the agent
 * wrote the folder) — the computed styles are what let a model reason about
 * "it looks wrong" at all. */
export type WuiPickDetail = {
  html?: string;
  marker?: string | null;
  rect?: { x: number; y: number; w: number; h: number };
  styles?: Record<string, string>;
};

export type WuiReport = {
  id: number;
  kind: WuiReportKind;
  message: string;
  detail: WuiPickDetail | null;
};

const KINDS: WuiReportKind[] = ["error", "refused", "pick"];

export function isWuiReportMessage(
  data: unknown,
): data is { report: WuiReportKind; message: string; detail: WuiPickDetail | null } {
  if (!data || typeof data !== "object") return false;
  const m = data as Record<string, unknown>;
  return (
    m.proto === WUI_PROTOCOL &&
    typeof m.message === "string" &&
    KINDS.includes(m.report as WuiReportKind)
  );
}

/** The one-line heading a person reads in the pane. */
export function reportHeadline(r: WuiReport): string {
  if (r.kind === "error") return `Something went wrong: ${r.message}`;
  if (r.kind === "refused") return `This page was not allowed to do that: ${r.message}`;
  return r.detail?.marker ? `You pointed at "${r.detail.marker}".` : "You pointed at part of the page.";
}

/**
 * The message handed to the agent.
 *
 * Written as something a person could have said, with the machine detail
 * underneath, because it arrives in the chat under their name — and because the
 * agent needs the styles far more than it needs our phrasing.
 */
export function formatReportsForAgent(
  folder: string,
  reports: WuiReport[],
  comment?: string,
): string {
  const where = folder || "the workspace root";
  const lines: string[] = [`About the WUI in ${where}:`];
  if (comment?.trim()) lines.push("", comment.trim());

  for (const r of reports) {
    lines.push("");
    if (r.kind === "pick") {
      lines.push("I pointed at this part of the page:");
      if (r.detail?.marker) lines.push(`- labelled: ${r.detail.marker}`);
      if (r.detail?.rect) {
        const { w, h, x, y } = r.detail.rect;
        lines.push(`- ${w}×${h} px at (${x}, ${y})`);
      }
      if (r.detail?.styles) {
        const styles = Object.entries(r.detail.styles)
          .filter(([, v]) => v)
          .map(([k, v]) => `${k}: ${v}`)
          .join("; ");
        if (styles) lines.push(`- computed style: ${styles}`);
      }
      // Capped HERE as well as in the runtime. The runtime's slice only binds
      // messages the runtime sent, and nothing stops a page posting its own
      // `report` — while the pane shows only the headline, so what a person
      // forwards under their own name would not be what they read.
      if (r.detail?.html) lines.push("```html", r.detail.html.slice(0, MAX_PICK_HTML), "```");
    } else if (r.kind === "refused") {
      lines.push(`The page was refused: ${r.message}`);
    } else {
      lines.push(`The page hit an error: ${r.message}`);
    }
  }
  return lines.join("\n");
}

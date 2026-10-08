import type { MsgKey, Vars } from "./i18n";

/** 「安裝 N 次 · 使用 M 次」 with the zero parts left out (plan-skill-hub-ux-redo
 * D17), or `null` when both are zero — the row, the sidebar and the chat
 * card say it the same way. */
export function countsText(
  t: (key: MsgKey, vars?: Vars) => string,
  installs: number,
  uses: number,
): string | null {
  const parts = [
    installs > 0 ? t("skillHub.counts.installs", { count: installs }) : null,
    uses > 0 ? t("skillHub.counts.uses", { count: uses }) : null,
  ].filter((p): p is string => p !== null);
  return parts.length > 0 ? parts.join(" · ") : null;
}

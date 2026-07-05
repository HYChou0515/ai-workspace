/**
 * Workspace storage usage bar (#245). A thin "X of Y used" gauge in the upload
 * area so a user sees they're filling up *before* an upload is rejected. Reads
 * the durable usage via TanStack Query (invalidated after every upload); hidden
 * when the workspace has no quota (`quota === 0`, unlimited).
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../../api";
import { qk } from "../../api/queryKeys";
import { formatBytes } from "../../lib/bytes";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

export function UsageBar({ slug, itemId }: { slug: string; itemId: string }) {
  const t = useT();
  const { data } = useQuery({
    queryKey: qk.workspaceUsage(slug, itemId),
    queryFn: () => api.getWorkspaceUsage(slug, itemId),
  });
  // Not loaded yet, or no quota (0 = unlimited) → no bar.
  if (!data || data.quota <= 0) return null;
  const pct = Math.min(100, Math.round((data.used / data.quota) * 100));
  const full = data.used >= data.quota;
  return (
    <div
      data-testid="workspace-usage"
      style={{ display: "flex", flexDirection: "column", gap: 2 }}
    >
      <div
        style={{
          height: 4,
          background: "var(--paper-3)",
          borderRadius: 2,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: full ? "var(--warn)" : "var(--accent)",
          }}
        />
      </div>
      <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
        {t("workspace.usage", {
          used: formatBytes(data.used),
          quota: formatBytes(data.quota),
        })}
      </span>
      {full && (
        <span style={{ fontSize: pxToRem(11), color: "var(--warn)" }}>
          {t("workspace.usage.full")}
        </span>
      )}
    </div>
  );
}

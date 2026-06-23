/**
 * The RCA-style topic/product chips shown in the workspace TopBar (#158). These
 * are item *attributes*, not hierarchy, so they stay in the page-local header
 * rather than the app-agnostic global breadcrumb. A topic chip deep-links to the
 * App dashboard filtered by that topic (`/a/{slug}?topic=…`) — the dashboard
 * seeds its filter from the param. Product has no dashboard filter, so it's
 * plain text, not a dead control (the old chips wrongly pointed at `/?…`, which
 * the launcher never read).
 *
 * Domain fields are typed `unknown` on AppItem; narrow the two this row reads.
 */

import { useNavigate } from "react-router-dom";

import type { AppItem, AppManifest } from "../../api/types";
import { Icon } from "../../components/Icon";

export function ItemCrumbChips({ item, manifest }: { item: AppItem; manifest: AppManifest }) {
  const navigate = useNavigate();
  const topics = (item.topics as string[] | undefined) ?? [];
  const product = String(item.product ?? "");
  return (
    <>
      {topics.map((t) => (
        <CrumbLink
          key={t}
          label={t}
          onClick={() => navigate(`/a/${manifest.slug}?topic=${encodeURIComponent(t)}`)}
          title={`Filter ${(manifest.item?.noun_plural ?? "items").toLowerCase()} by topic “${t}”`}
        />
      ))}
      {product && (
        <>
          <Icon name="chev_r" size={12} color="var(--text-paper-d2)" />
          <span style={{ color: "var(--text-paper-d)", padding: "1px 4px" }}>{product}</span>
        </>
      )}
    </>
  );
}

function CrumbLink({ label, onClick, title }: { label: string; onClick: () => void; title?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      style={{
        color: "var(--text-paper-d)",
        fontSize: "var(--text-body-sm)",
        padding: "1px 4px",
        borderRadius: 3,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.color = "var(--accent-h)";
        e.currentTarget.style.background = "var(--paper-2)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.color = "var(--text-paper-d)";
        e.currentTarget.style.background = "transparent";
      }}
    >
      {label}
    </button>
  );
}

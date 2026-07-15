/**
 * Create an App item (`/a/:slug/new`) — #89 P6 + P7b, styled to the
 * design-handoff "Start an RCA" modal (`design_handoff_rca_3.0`,
 * `NewInvestigation`). Rendered as a nested route under the dashboard so it
 * appears as a centered modal over the live dashboard (NOT a standalone page).
 *
 * The card is a fixed header (caps label + title + close ✕) / scrollable body
 * (the schema-driven {@link ItemForm}: title → field grid with owner → template
 * cards → description) / pinned footer (Cancel + Create). The footer's Create
 * button submits the form via `form={FORM_ID}` so it can live outside the
 * scroll area. Empty fields are stripped so omitted domain fields take their
 * backend defaults. POSTs to createAppItem (create + seed the chosen profile)
 * then goes straight into the new item.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { CSSProperties } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";
import { ItemForm, pruneEmpty } from "../components/ItemForm";
import { ModalShell } from "../components/ModalShell";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useAppManifest } from "../hooks/useResources";
import { pxToRem } from "../lib/pxToRem";

const FORM_ID = "new-item-form";

const ghostBtn: CSSProperties = {
  height: 36,
  padding: "0 14px",
  fontSize: pxToRem(13),
  fontWeight: 500,
  fontFamily: "inherit",
  color: "var(--text-paper-d)",
  background: "transparent",
  border: "1px solid transparent",
  borderRadius: "var(--radius-btn)",
  cursor: "pointer",
};

function CapsLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: pxToRem(10), fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--text-paper-d)", marginBottom: 6 }}>
      {children}
    </div>
  );
}

export function AppNewItem() {
  const { slug = "" } = useParams();
  const manifest = useAppManifest(slug);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const me = useCurrentUser();

  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.createAppItem(slug, values),
    onSuccess: (data) => {
      // Refresh the dashboard list (so the new item shows when you return) and
      // go straight INTO the new item's workspace.
      void qc.invalidateQueries({ queryKey: qk.appItems(slug) });
      navigate(`/a/${slug}/${encodeURIComponent(data.resource_id)}`);
    },
  });

  const close = () => navigate(`/a/${slug}`);
  const noun = manifest?.item.noun ?? "item";
  const article = /^[aeiou]/i.test(noun) ? "an" : "a";

  return (
    <ModalShell
      onClose={close}
      ariaLabel={`Start ${article} ${noun.toLowerCase()}`}
      data-testid="page-app-new"
      width={620}
      maxWidth="100%"
      panelStyle={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        boxShadow: "0 12px 48px rgba(20,22,28,0.12)",
      }}
    >
      {manifest && (
        <>
          {/* Header (fixed) */}
          <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid var(--paper-3)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <CapsLabel>New {noun}</CapsLabel>
              <h2 style={{ fontSize: pxToRem(22), fontWeight: 800, margin: "6px 0 0", letterSpacing: "-0.02em" }}>
                Start {article} {noun.toLowerCase()}
              </h2>
            </div>
            <button type="button" aria-label="Close" onClick={close} style={{ ...ghostBtn, display: "inline-flex", alignItems: "center", height: 28, padding: "0 10px" }}>
              <Icon name="x" size={14} />
            </button>
          </div>

          {/* Body (scrolls) */}
          <div style={{ padding: "20px 22px", overflow: "auto" }}>
            <ItemForm
              manifest={manifest}
              profiles={manifest.profiles}
              defaultProfile={manifest.default_profile}
              ownerId={me}
              formId={FORM_ID}
              hideFooter
              submitLabel="Create"
              onSubmit={(values) => {
                if (!String(values.title ?? "").trim()) return;
                create.mutate(pruneEmpty(values));
              }}
            />
          </div>

          {/* Footer (fixed) */}
          <div style={{ padding: "14px 22px", borderTop: "1px solid var(--paper-3)", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, background: "var(--paper-2)" }}>
            {create.isError && (
              // Surface a failed create instead of silently flipping the button back
              // to "Create" — a swallowed 4xx/5xx used to look like nothing happened.
              <div role="alert" style={{ marginRight: "auto", fontSize: pxToRem(12), color: "var(--err)" }}>
                {create.error instanceof Error ? create.error.message : "Couldn’t create — please try again."}
              </div>
            )}
            <button type="button" className="btn" data-variant="ghost" data-size="md" onClick={close}>
              Cancel
            </button>
            <button type="submit" form={FORM_ID} disabled={create.isPending} className="btn" data-variant="primary" data-size="md">
              {create.isPending ? "Saving…" : "Create"}
            </button>
          </div>
        </>
      )}
    </ModalShell>
  );
}

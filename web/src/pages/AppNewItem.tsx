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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type CSSProperties } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import { skillHubApi } from "../api/skillHub";
import { Icon } from "../components/Icon";
import { ItemForm, pruneEmpty } from "../components/ItemForm";
import { ModalShell } from "../components/ModalShell";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useAppManifest } from "../hooks/useResources";
import { useT } from "../lib/i18n";
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
    <div
      style={{
        fontSize: pxToRem(10),
        fontWeight: 700,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        color: "var(--text-paper-d)",
        marginBottom: 6,
      }}
    >
      {children}
    </div>
  );
}

export function AppNewItem() {
  const { slug = "" } = useParams();
  const manifest = useAppManifest(slug);
  // `?profile=<name>`: a caller that knows which profile the item should
  // start on (the skill hub's "edit in a new item", plan-skill-hub P8)
  // says so in the address. Honoured only when the App ships it; anything
  // else falls back to the App's default, as before.
  const [params] = useSearchParams();
  const asked = params.get("profile");
  const askedProfile =
    asked && manifest?.profiles.some((p) => p.name === asked)
      ? asked
      : undefined;
  // `?skill=<entry id>`: the skill hub's "edit in a new item" says which
  // skill this item is for, and the form says what comes next — install it
  // from the Skills panel (plan-skill-hub-ui-polish D11). Said, not done:
  // creating is this form's, installing is the panel's, and both stay
  // visible. The entry is read for its name; until it lands (or if it never
  // does) the hint still says "this skill".
  const t = useT();
  const skillId = params.get("skill") ?? "";
  const skillQ = useQuery({
    queryKey: qk.skillHubEntry(skillId, slug),
    queryFn: () => skillHubApi.get(skillId, slug),
    enabled: skillId !== "",
  });
  const skillLabel = skillQ.data
    ? `${skillQ.data.owner}/${skillQ.data.name}`
    : t("newItem.skillHint.this");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const me = useCurrentUser();

  const create = useMutation({
    // Renders its own error under the form (`create.isError` below).
    meta: { silentError: true },
    mutationFn: (values: Record<string, unknown>) =>
      api.createAppItem(slug, values),
    onSuccess: (data) => {
      // Refresh the dashboard list (so the new item shows when you return) and
      // go straight INTO the new item's workspace.
      void qc.invalidateQueries({ queryKey: qk.appItems(slug) });
      navigate(`/a/${slug}/${encodeURIComponent(data.resource_id)}`);
    },
  });

  const close = () => navigate(`/a/${slug}`);
  // #779: a create form is unsaved work by definition, so both deliberate exits
  // (Escape via the shell, and the ✕ below) run through the same guard.
  const [dirty, setDirty] = useState(false);
  const attemptClose = useDirtyClose(dirty, close);
  const noun = manifest?.item.noun ?? "item";
  const article = /^[aeiou]/i.test(noun) ? "an" : "a";

  return (
    <ModalShell
      onClose={attemptClose}
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
          <div
            style={{
              padding: "18px 22px 14px",
              borderBottom: "1px solid var(--paper-3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div>
              <CapsLabel>New {noun}</CapsLabel>
              <h2
                style={{
                  fontSize: pxToRem(22),
                  fontWeight: 800,
                  margin: "6px 0 0",
                  letterSpacing: "-0.02em",
                }}
              >
                Start {article} {noun.toLowerCase()}
              </h2>
            </div>
            <button
              type="button"
              aria-label="Close"
              onClick={attemptClose}
              style={{
                ...ghostBtn,
                display: "inline-flex",
                alignItems: "center",
                height: 28,
                padding: "0 10px",
              }}
            >
              <Icon name="x" size={14} />
            </button>
          </div>

          {/* Body (scrolls) */}
          <div
            className="scrollable"
            style={{ padding: "20px 22px", overflow: "auto" }}
          >
            {skillId !== "" && (
              <p
                data-testid="new-item-skill-hint"
                className="hint"
                style={{ margin: "0 0 14px", fontSize: pxToRem(12) }}
              >
                {t("newItem.skillHint", { skill: skillLabel })}
              </p>
            )}
            <ItemForm
              manifest={manifest}
              profiles={manifest.profiles}
              defaultProfile={askedProfile ?? manifest.default_profile}
              ownerId={me}
              formId={FORM_ID}
              hideFooter
              onDirtyChange={setDirty}
              submitLabel="Create"
              onSubmit={(values) => {
                if (!String(values.title ?? "").trim()) return;
                create.mutate(pruneEmpty(values));
              }}
            />
          </div>

          {/* Footer (fixed) */}
          <div
            style={{
              padding: "14px 22px",
              borderTop: "1px solid var(--paper-3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              gap: 8,
              background: "var(--paper-2)",
            }}
          >
            {create.isError && (
              // Surface a failed create instead of silently flipping the button back
              // to "Create" — a swallowed 4xx/5xx used to look like nothing happened.
              <div
                role="alert"
                style={{
                  marginRight: "auto",
                  fontSize: pxToRem(12),
                  color: "var(--err)",
                }}
              >
                {create.error instanceof Error
                  ? create.error.message
                  : "Couldn’t create — please try again."}
              </div>
            )}
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              data-size="md"
              onClick={attemptClose}
            >
              Cancel
            </button>
            <button
              type="submit"
              form={FORM_ID}
              disabled={create.isPending}
              className="btn"
              data-variant="primary"
              data-size="md"
            >
              {create.isPending ? "Saving…" : "Create"}
            </button>
          </div>
        </>
      )}
    </ModalShell>
  );
}

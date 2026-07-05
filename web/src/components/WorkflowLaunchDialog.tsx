/**
 * Pre-flight launch dialog (#283) — pressing "Run" opens THIS first, not the run.
 * It answers the two launch traps the issue calls out: "which workflow / what does it
 * do" and "did I need to prepare inputs". It shows the workflow's summary + phases and
 * the author's pre-flight checklist (`runs/preview`), and blocks "Run" while a REQUIRED
 * precondition fails — so an empty-uploads no-op is caught before it wastes a run.
 *
 * Opened from the topic-hub `NewItemPicker` (every App runs through the multi-chat
 * `ItemChatShell` now, #200): it renders this with the chosen `workflowId` and fires
 * `onConfirm` (the real startRun) only once the operator confirms a runnable preview.
 */

import type { PreflightCheckDTO } from "../api/workflows";
import { usePreviewRun } from "../hooks/useWorkflow";
import { useT } from "../lib/i18n";
import { Icon } from "./Icon";
import { ModalShell } from "./ModalShell";
import { pxToRem } from "../lib/pxToRem";

export function WorkflowLaunchDialog({
  slug,
  itemId,
  workflowId,
  onConfirm,
  onClose,
}: {
  slug: string;
  itemId: string;
  workflowId: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const preview = usePreviewRun(slug, itemId, workflowId, true);
  const p = preview.data;
  const canRun = !!p?.can_run;

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={p?.title || t("wf.launch.title")}
      data-testid="wf-launch-dialog"
      width={460}
      maxWidth="92vw"
      panelStyle={{
        padding: 20,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
            {t("wf.launch.title")}
          </span>
          <strong style={{ fontSize: pxToRem(15) }}>{p?.title || workflowId}</strong>
          {p?.description && (
            <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
              {p.description}
            </span>
          )}
        </div>

        {preview.isLoading && (
          <p data-testid="wf-launch-loading" style={{ fontSize: pxToRem(12), margin: 0 }}>
            {t("wf.launch.loading")}
          </p>
        )}
        {preview.isError && (
          <p style={{ fontSize: pxToRem(12), margin: 0, color: "var(--err)" }}>
            {t("wf.launch.error")}
          </p>
        )}

        {p && (
          <>
            {p.summary && (
              <div
                data-testid="wf-launch-summary"
                style={{
                  padding: "8px 10px",
                  background: "var(--paper-2)",
                  borderRadius: 6,
                  fontSize: pxToRem(12.5),
                  lineHeight: 1.55,
                  color: "var(--text-paper)",
                }}
              >
                {p.summary}
              </div>
            )}

            {p.phases.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
                  {t("wf.launch.steps")}
                </span>
                {p.phases.map((ph, i) => (
                  <span key={ph.id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {i > 0 && <span style={{ color: "var(--text-paper-d2)" }}>›</span>}
                    <span
                      style={{
                        fontSize: pxToRem(11),
                        padding: "2px 8px",
                        borderRadius: 999,
                        background: "var(--paper-2)",
                        color: "var(--text-paper-d)",
                      }}
                    >
                      {ph.title || ph.id}
                    </span>
                  </span>
                ))}
              </div>
            )}

            {p.checks.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d2)" }}>
                  {t("wf.launch.checklist")}
                </span>
                <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                  {p.checks.map((c, i) => (
                    <CheckRow key={i} check={c} />
                  ))}
                </ul>
              </div>
            )}

            {!canRun && (
              <div
                data-testid="wf-launch-blocked"
                style={{
                  fontSize: pxToRem(12),
                  color: "var(--err)",
                  background: "var(--paper-2)",
                  borderLeft: "2px solid var(--err)",
                  borderRadius: 6,
                  padding: "8px 10px",
                }}
              >
                {t("wf.launch.blocked")}
              </div>
            )}
          </>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            data-testid="wf-launch-cancel"
            onClick={onClose}
            style={btnStyle(false)}
          >
            {t("wf.launch.cancel")}
          </button>
          <button
            type="button"
            data-testid="wf-launch-run"
            disabled={!canRun}
            onClick={() => canRun && onConfirm()}
            style={btnStyle(true, !canRun)}
          >
            {t("wf.launch.run")}
          </button>
        </div>
    </ModalShell>
  );
}

function CheckRow({ check }: { check: PreflightCheckDTO }) {
  const t = useT();
  const failed = !check.ok;
  const advisory = check.severity === "advisory";
  // ok → green check; failed required → red; failed advisory → amber. The required vs
  // advisory distinction also reads in the badge label below, so the icon stays check/x.
  const tone = !failed ? "var(--ok)" : advisory ? "var(--warn)" : "var(--err)";
  const icon = failed ? "x" : "check";
  return (
    <li style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: pxToRem(12.5) }}>
      <span style={{ color: tone, marginTop: 1, flex: "0 0 auto" }}>
        <Icon name={icon} size={14} />
      </span>
      <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <span style={{ color: "var(--text-paper)" }}>
          {check.label}
          {failed && (
            <span style={{ marginLeft: 6, fontSize: pxToRem(10), color: tone }}>
              · {t(advisory ? "wf.launch.advisory" : "wf.launch.required")}
            </span>
          )}
        </span>
        {failed && check.reason && (
          <span style={{ color: "var(--text-paper-d)", fontSize: pxToRem(11.5) }}>
            {check.reason}
          </span>
        )}
      </span>
    </li>
  );
}

function btnStyle(primary: boolean, disabled = false): React.CSSProperties {
  const base: React.CSSProperties = {
    height: 30,
    padding: "0 16px",
    borderRadius: "var(--radius-btn)",
    fontSize: pxToRem(13),
    cursor: disabled ? "not-allowed" : "pointer",
    border: "1px solid var(--paper-3)",
    background: "var(--white)",
    color: "var(--text-paper)",
  };
  if (primary) {
    return {
      ...base,
      background: disabled ? "var(--paper-3)" : "var(--accent)",
      borderColor: disabled ? "var(--paper-3)" : "var(--accent)",
      color: disabled ? "var(--text-paper-d2)" : "var(--white)",
      fontWeight: 600,
      opacity: disabled ? 0.7 : 1,
    };
  }
  return base;
}

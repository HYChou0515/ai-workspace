import { useEffect, useRef, useState } from "react";

import type { WorkflowManifestDTO } from "../api/workflows";
import { Icon } from "./Icon";

/**
 * The "Run workflow" launcher (topic-hub §4, §12): a primary-action dropdown that
 * lists every workflow the profile offers as a rich card — a dark icon tile, the
 * title, a kind pill (`batch` / `single`), a one-line description, and an accent
 * inputs hint. Distinct from the [Free chat] picker (NewChatPicker) — launching a
 * workflow is the headless, API-triggerable path, so it gets the red button.
 * Presentational; the parent wires the actual run (`onLaunch(id)`).
 */
export function RunWorkflowPicker({
  workflows,
  onLaunch,
  disabled = false,
}: {
  workflows: WorkflowManifestDTO[];
  onLaunch: (id: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape (a menu, not a modal).
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // An interactive profile offers no workflows → nothing to launch.
  if (!workflows.length) return null;

  const choose = (id: string) => {
    setOpen(false);
    onLaunch(id);
  };

  return (
    <div className="run-workflow-picker" ref={rootRef}>
      <button
        type="button"
        className="run-workflow-picker__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        data-testid="run-workflow-button"
      >
        <Icon name="play" size={12} color="var(--white)" />
        <span>Run workflow</span>
        <Icon name="chev_d" size={12} color="var(--white)" />
      </button>
      {open && (
        <div
          role="menu"
          className="run-workflow-picker__menu"
          data-testid="run-workflow-menu"
        >
          <div className="run-workflow-picker__header">Workflows on this profile</div>
          {workflows.map((wf) => {
            const single = wf.tag === "single";
            return (
              <button
                key={wf.id}
                type="button"
                role="menuitem"
                className="run-workflow-picker__card"
                onClick={() => choose(wf.id)}
                data-testid={`run-workflow-card-${wf.id}`}
              >
                <span className="run-workflow-picker__icon" aria-hidden>
                  <Icon name={single ? "file" : "layers"} size={16} color="var(--white)" />
                </span>
                <span className="run-workflow-picker__body">
                  <span className="run-workflow-picker__titleRow">
                    <span className="run-workflow-picker__title">{wf.title || wf.id}</span>
                    {wf.tag && <span className="run-workflow-picker__tag">{wf.tag}</span>}
                  </span>
                  {wf.description && (
                    <span className="run-workflow-picker__desc">{wf.description}</span>
                  )}
                  {wf.hint && <span className="run-workflow-picker__hint">{wf.hint}</span>}
                </span>
              </button>
            );
          })}
          <div className="run-workflow-picker__footer">
            Headless · API-triggerable · you approve before any commit.
          </div>
        </div>
      )}
    </div>
  );
}

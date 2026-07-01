import { useEffect, useRef, useState } from "react";

import type { WorkflowManifestDTO } from "../api/workflows";
import { useT } from "../lib/i18n";
import { Icon } from "./Icon";

/**
 * The "run a workflow IN this chat" launcher (#343). Unlike `NewItemPicker` (which
 * opens a FRESH chat), this takes over the CURRENT chat: the user prepares in the
 * chat, then picks a workflow here to run right in the same thread. Presentational —
 * the parent wires the takeover launch (via `WorkflowLaunchDialog` → `startRun` with
 * the chat's id). Rendered only when the chat has no active run.
 */
export function WorkflowLaunchMenu({
  workflows,
  onPick,
}: {
  workflows: WorkflowManifestDTO[];
  onPick: (id: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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

  const pick = (id: string) => {
    setOpen(false);
    onPick(id);
  };

  return (
    <div className="wf-launch-menu" ref={rootRef}>
      <button
        type="button"
        className="wf-launch-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        data-testid="launch-in-chat-button"
      >
        <Icon name="settings" size={12} color="var(--text-paper-d)" />
        <span>{t("wf.launchHere.trigger")}</span>
        <Icon name="chev_d" size={12} color="var(--text-paper-d)" />
      </button>
      {open && (
        <div role="menu" className="wf-launch-menu__menu" data-testid="launch-in-chat-menu">
          {workflows.map((wf) => (
            <button
              key={wf.id}
              type="button"
              role="menuitem"
              className="wf-launch-menu__workflow"
              data-testid={`launch-in-chat-workflow-${wf.id}`}
              onClick={() => pick(wf.id)}
            >
              <Icon name="settings" size={14} color="var(--text-paper-d)" />
              <span className="wf-launch-menu__wf-body">
                <span className="wf-launch-menu__wf-title">{wf.title || wf.id}</span>
                {wf.description && <span className="wf-launch-menu__wf-desc">{wf.description}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

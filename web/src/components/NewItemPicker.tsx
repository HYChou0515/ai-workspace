import { useEffect, useRef, useState } from "react";

import type { WorkflowManifestDTO } from "../api/workflows";
import { Icon } from "./Icon";

/**
 * The single "+ New" launcher (#132, topic-hub §4) — one menu that opens a free
 * chat OR launches one of the profile's workflows, replacing the separate
 * `NewChatPicker` + `RunWorkflowPicker`. A profile with no workflows still offers
 * Free chat. Presentational — the parent wires the create / run.
 */
export function NewItemPicker({
  workflows,
  onFreeChat,
  onWorkflow,
  disabled = false,
}: {
  workflows: WorkflowManifestDTO[];
  onFreeChat: () => void;
  onWorkflow: (id: string) => void;
  disabled?: boolean;
}) {
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

  const freeChat = () => {
    setOpen(false);
    onFreeChat();
  };
  const workflow = (id: string) => {
    setOpen(false);
    onWorkflow(id);
  };

  return (
    <div className="new-item-picker" ref={rootRef}>
      <button
        type="button"
        className="new-item-picker__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        data-testid="new-item-button"
      >
        <Icon name="plus" size={12} color="var(--text-paper-d)" />
        <span>New</span>
        <Icon name="chev_d" size={12} color="var(--text-paper-d)" />
      </button>
      {open && (
        <div role="menu" className="new-item-picker__menu" data-testid="new-item-menu">
          <button
            type="button"
            role="menuitem"
            className="new-item-picker__free"
            data-testid="new-item-free"
            onClick={freeChat}
          >
            <Icon name="chat" size={14} color="var(--text-paper-d)" />
            <span>Free chat</span>
          </button>
          {workflows.length > 0 && (
            <>
              <div className="new-item-picker__divider" role="separator" />
              {workflows.map((wf) => (
                <button
                  key={wf.id}
                  type="button"
                  role="menuitem"
                  className="new-item-picker__workflow"
                  data-testid={`new-item-workflow-${wf.id}`}
                  onClick={() => workflow(wf.id)}
                >
                  <Icon name="settings" size={14} color="var(--text-paper-d)" />
                  <span className="new-item-picker__wf-body">
                    <span className="new-item-picker__wf-title">{wf.title || wf.id}</span>
                    {wf.description && (
                      <span className="new-item-picker__wf-desc">{wf.description}</span>
                    )}
                  </span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

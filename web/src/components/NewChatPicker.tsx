import { useState } from "react";

import type { WorkflowManifestDTO } from "../api/workflows";

/**
 * The "new chat" picker (topic-hub §3/§4): opening a chat is choosing **[Free chat]**
 * or one of the seed profile's **workflows**. A free chat is human-driven; a workflow
 * chat is run-driven (the orchestrator drives its turns). Presentational — the parent
 * wires the actual create / run.
 */
export function NewChatPicker({
  workflows,
  onFreeChat,
  onWorkflow,
  disabled = false,
}: {
  workflows: WorkflowManifestDTO[];
  onFreeChat: () => void;
  onWorkflow: (workflowId: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  const choose = (run: () => void) => {
    setOpen(false);
    run();
  };

  return (
    <div className="new-chat-picker" style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        data-testid="new-chat-button"
      >
        + New chat
      </button>
      {open && (
        <div role="menu" data-testid="new-chat-menu" style={{ position: "absolute", zIndex: 10 }}>
          <button type="button" role="menuitem" onClick={() => choose(onFreeChat)}>
            Free chat
          </button>
          {workflows.map((wf) => (
            <button
              key={wf.id}
              type="button"
              role="menuitem"
              onClick={() => choose(() => onWorkflow(wf.id))}
            >
              {wf.title || wf.id}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

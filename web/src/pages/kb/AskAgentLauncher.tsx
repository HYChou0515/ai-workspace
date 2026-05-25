/**
 * AskAgentLauncher — the global "Ask agent" button (top bar) that opens the
 * fast-chat drawer, plus the doc viewer a citation opens. Self-contained so any
 * page can drop it in; "manage sources" jumps to the KB page.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { AskAgentDrawer } from "./AskAgentDrawer";
import { KbDocViewer } from "./KbDocViewer";

export function AskAgentLauncher({ client = kbApi }: { client?: KbApi }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [viewer, setViewer] = useState<{ documentId: string; snippet?: string } | null>(null);

  return (
    <>
      <button
        type="button"
        className="kb-btn"
        style={{ background: "var(--ink)", color: "var(--text-dark)", border: "none" }}
        onClick={() => setOpen(true)}
      >
        <Icon name="sparkle" size={13} color="var(--accent)" /> Ask agent
      </button>

      <AskAgentDrawer
        open={open}
        onClose={() => setOpen(false)}
        onManage={() => {
          setOpen(false);
          navigate("/kb");
        }}
        onHistory={() => {
          setOpen(false);
          navigate("/kb?tab=chats");
        }}
        onOpenCitation={(c: KbCitation) =>
          setViewer({ documentId: c.document_id, snippet: c.snippet })
        }
        client={client}
      />

      {viewer && (
        <KbDocViewer
          documentId={viewer.documentId}
          snippet={viewer.snippet}
          onClose={() => setViewer(null)}
          client={client}
        />
      )}
    </>
  );
}

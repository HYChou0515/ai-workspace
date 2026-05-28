/**
 * AskAgentLauncher — the "Ask agent" button used on both Home (TopBar) and
 * the KB shell (KbHome topbar). Opens the fast-chat drawer; renders a
 * citation viewer when a citation is followed.
 *
 * `onManage` / `onHistory` / `onOpenCitation` are overridable so the KB
 * shell can switch its own tab + reuse its own viewer (instead of navigating
 * away or popping a second overlay). Home leaves them off — the defaults
 * navigate to /kb and use the launcher's own viewer.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { AskAgentDrawer } from "./AskAgentDrawer";
import { KbDocViewer } from "./KbDocViewer";

export function AskAgentLauncher({
  client = kbApi,
  onManage,
  onHistory,
  onOpenCitation,
}: {
  client?: KbApi;
  /** Override "manage sources" — defaults to navigating to /kb. */
  onManage?: () => void;
  /** Override "history" — defaults to navigating to /kb?tab=chats. */
  onHistory?: () => void;
  /** Override the citation handler — if set, the launcher does NOT render its
   * own viewer (the caller handles it). Defaults to the internal viewer. */
  onOpenCitation?: (c: KbCitation) => void;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [viewer, setViewer] = useState<{ documentId: string; snippet?: string } | null>(null);

  const handleManage = () => {
    setOpen(false);
    if (onManage) onManage();
    else navigate("/kb");
  };
  const handleHistory = () => {
    setOpen(false);
    if (onHistory) onHistory();
    else navigate("/kb?tab=chats");
  };
  const handleCitation = (c: KbCitation) => {
    if (onOpenCitation) onOpenCitation(c);
    else setViewer({ documentId: c.document_id, snippet: c.snippet });
  };

  return (
    <>
      <button type="button" className="kb-btn kb-btn--primary" onClick={() => setOpen(true)}>
        <Icon name="sparkle" size={14} /> Ask agent
      </button>

      <AskAgentDrawer
        open={open}
        onClose={() => setOpen(false)}
        onManage={handleManage}
        onHistory={handleHistory}
        onOpenCitation={handleCitation}
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

/**
 * "Ask agent" drawer — the FAST chat: a slide-in from the right for a quick,
 * throwaway question to the KB agent (a fresh thread each time it's opened).
 * Full, persistent conversations live in the Chats page (KbChatView), not here.
 */

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { KbChatPanel } from "./KbChatPanel";

export function AskAgentDrawer({
  open,
  onClose,
  onOpenCitation,
  onManage,
  client = kbApi,
}: {
  open: boolean;
  onClose: () => void;
  onOpenCitation?: (c: KbCitation) => void;
  onManage?: () => void;
  client?: KbApi;
}) {
  if (!open) return null;
  return (
    <>
      <div onClick={onClose} className="kb-drawer-backdrop" aria-hidden />
      <aside className="kb-drawer" role="dialog" aria-label="Ask the knowledge base">
        <header className="kb-drawer__head">
          <div className="kb-drawer__mark">
            <Icon name="sparkle" size={16} color="var(--accent)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="kb-drawer__title">Ask the knowledge base</div>
            <button type="button" className="kb-drawer__manage" onClick={onManage}>
              manage sources
            </button>
          </div>
          <button type="button" className="kb-iconbtn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>
        {/* key={open} resets to a fresh thread each time the drawer reopens */}
        <KbChatPanel key={String(open)} chatId={null} onOpenCitation={onOpenCitation} client={client} />
      </aside>
    </>
  );
}

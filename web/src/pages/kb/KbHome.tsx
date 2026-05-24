/**
 * KB shell (route /kb) — sidebar nav between the collections-management and
 * chat-history surfaces, a top-bar "Ask agent" entry, and the drawer + doc
 * viewer overlays wired together: citations and document rows both open the
 * viewer; chat rows open the drawer on that thread.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { Icon } from "../../components/Icon";
import { AskAgentDrawer } from "./AskAgentDrawer";
import { KbChatsPage } from "./KbChatsPage";
import { KbCollectionsPage } from "./KbCollectionsPage";
import { KbDocViewer } from "./KbDocViewer";

type Tab = "collections" | "chats";

type Viewer = { documentId: string; snippet?: string };

export function KbHome({ client = kbApi }: { client?: KbApi }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("collections");
  const [allCollectionIds, setAllCollectionIds] = useState<string[]>([]);
  const [drawer, setDrawer] = useState<{ open: boolean; chatId: string | null }>({
    open: false,
    chatId: null,
  });
  const [viewer, setViewer] = useState<Viewer | null>(null);

  useEffect(() => {
    client.listCollections().then((cols) => setAllCollectionIds(cols.map((c) => c.resource_id)));
  }, [client, drawer.open]);

  const openCitation = (c: KbCitation) => setViewer({ documentId: c.document_id, snippet: c.snippet });

  return (
    <div className="kb-shell">
      <aside className="kb-nav">
        <div className="kb-nav__brand">Knowledge base</div>
        <button
          type="button"
          className={`kb-nav__item${tab === "collections" ? " is-active" : ""}`}
          onClick={() => setTab("collections")}
        >
          <Icon name="layers" size={15} /> Collections
        </button>
        <button
          type="button"
          className={`kb-nav__item${tab === "chats" ? " is-active" : ""}`}
          onClick={() => setTab("chats")}
        >
          <Icon name="chat" size={15} /> Chats
        </button>
        <button type="button" className="kb-nav__back" onClick={() => navigate("/")}>
          <Icon name="chev_l" size={13} /> Investigations
        </button>
      </aside>

      <main className="kb-main">
        <header className="kb-topbar">
          <span className="kb-topbar__title">
            {tab === "collections" ? "Collections" : "Conversations"}
          </span>
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            onClick={() => setDrawer({ open: true, chatId: null })}
          >
            <Icon name="sparkle" size={14} /> Ask agent
          </button>
        </header>

        <div className="kb-surface">
          {tab === "collections" ? (
            <KbCollectionsPage client={client} onOpenDoc={(id) => setViewer({ documentId: id })} />
          ) : (
            <KbChatsPage
              client={client}
              onOpenChat={(chatId) => setDrawer({ open: true, chatId })}
              onNewChat={() => setDrawer({ open: true, chatId: null })}
            />
          )}
        </div>
      </main>

      <AskAgentDrawer
        key={drawer.chatId ?? "new"}
        open={drawer.open}
        chatId={drawer.chatId}
        collectionIds={allCollectionIds}
        onClose={() => setDrawer({ open: false, chatId: null })}
        onManage={() => {
          setDrawer({ open: false, chatId: null });
          setTab("collections");
        }}
        onOpenCitation={openCitation}
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
    </div>
  );
}

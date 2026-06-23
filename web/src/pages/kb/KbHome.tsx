/**
 * KB shell (route /kb) — sidebar nav between collections-management and chat
 * history. The Chats tab is a list + a full-page conversation (KbChatView) for
 * the selected/new thread (NOT a drawer). The top-bar "Ask agent" opens the
 * fast-chat drawer for a quick throwaway question. Citations and document rows
 * open the doc viewer overlay.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { HealthDot } from "../../components/HealthDot";
import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { AskAgentLauncher } from "./AskAgentLauncher";
import { KbChatsPage } from "./KbChatsPage";
import { KbChatView } from "./KbChatView";
import { KbCollectionsPage } from "./KbCollectionsPage";
import { KbDocViewer } from "./KbDocViewer";

type Tab = "collections" | "chats";
type Viewer = { documentId: string; snippet?: string };
// undefined = nothing selected yet; null = a new chat; string = an existing one.
type Selected = string | null | undefined;

export function KbHome({ client = kbApi }: { client?: KbApi }) {
  const navigate = useNavigate();
  const t = useT();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<Tab>(params.get("tab") === "chats" ? "chats" : "collections");
  const [chatId, setChatId] = useState<Selected>(undefined);
  const [viewKey, setViewKey] = useState(0);
  const [chatListVersion, setChatListVersion] = useState(0);
  const [viewer, setViewer] = useState<Viewer | null>(null);

  const openCite = (c: KbCitation) => setViewer({ documentId: c.document_id, snippet: c.snippet });
  // Explicitly open a thread (or a new one): remount the view.
  const openThread = (id: string | null) => {
    setChatId(id);
    setViewKey((v) => v + 1);
  };
  // A new thread (first message) should appear in the list right away and its
  // row should highlight — but DON'T bump viewKey (no remount mid-stream).
  const onChatCreated = (id: string) => {
    setChatId(id);
    setChatListVersion((v) => v + 1);
  };

  return (
    <div className="kb-shell">
      <aside className="kb-nav">
        <div className="kb-nav__brand">{t("kb.brand")}</div>
        <button
          type="button"
          className={`kb-nav__item${tab === "collections" ? " is-active" : ""}`}
          onClick={() => setTab("collections")}
        >
          <Icon name="layers" size={15} /> {t("kb.collections")}
        </button>
        <button
          type="button"
          className={`kb-nav__item${tab === "chats" ? " is-active" : ""}`}
          onClick={() => setTab("chats")}
        >
          <Icon name="chat" size={15} /> {t("kb.chats")}
        </button>
        <button type="button" className="kb-nav__back" onClick={() => navigate("/")}>
          <Icon name="chev_l" size={13} /> {t("kb.back")}
        </button>
      </aside>

      <main className="kb-main">
        <header className="kb-topbar">
          <span className="kb-topbar__title">
            {tab === "collections" ? t("kb.collections") : t("kb.conversations")}
          </span>
          <HealthDot />
          {/* Same component Home uses — switches our own tab on
              manage/history and reuses our viewer for citations. */}
          <AskAgentLauncher
            client={client}
            onManage={() => setTab("collections")}
            onHistory={() => setTab("chats")}
            onOpenCitation={openCite}
          />
        </header>

        <div className="kb-surface">
          {tab === "collections" ? (
            <KbCollectionsPage client={client} onOpenDoc={(id) => setViewer({ documentId: id })} />
          ) : (
            <div className="kb-chats-split">
              <KbChatsPage
                client={client}
                selectedId={chatId ?? undefined}
                refreshSignal={chatListVersion}
                onOpenChat={(id) => openThread(id)}
                onNewChat={() => openThread(null)}
              />
              <div className="kb-chats-split__view">
                {chatId === undefined ? (
                  <div className="kb-chats-split__empty">{t("kb.empty")}</div>
                ) : (
                  // Keyed by viewKey (not chatId) so a fresh thread getting its
                  // id mid-turn doesn't remount and kill the stream.
                  <KbChatView
                    key={viewKey}
                    chatId={chatId}
                    onOpenCitation={openCite}
                    onChatCreated={onChatCreated}
                    client={client}
                  />
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {viewer && (
        <KbDocViewer
          documentId={viewer.documentId}
          snippet={viewer.snippet}
          onClose={() => setViewer(null)}
          onChanged={() => void qc.invalidateQueries({ queryKey: qk.kb.all })}
          client={client}
        />
      )}
    </div>
  );
}

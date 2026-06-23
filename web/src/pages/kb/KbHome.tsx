/**
 * KB shell (route /kb) — the layout that frames every KB view: a sidebar that
 * switches between collections-management and chat history, a top bar with the
 * "Ask agent" launcher, and a surface that renders the matched child route
 * (#93 — collections / a collection / chats all live at their own URLs).
 *
 * The citation/doc viewer overlay lives here (one per shell, over any child)
 * and is exposed to children through the Outlet context (useKbOutlet); a later
 * phase moves it onto a `?doc=` search param.
 */

import { useQueryClient } from "@tanstack/react-query";
import { Outlet, useLocation, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";

import { kbApi, type KbApi, type KbCitation } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { HealthDot } from "../../components/HealthDot";
import { Icon } from "../../components/Icon";
import { AskAgentLauncher } from "./AskAgentLauncher";
import { KbDocViewer } from "./KbDocViewer";

/** What the shell shares with its routed children: the doc-viewer openers
 * (the overlay itself is owned by the shell). */
export type KbOutletCtx = {
  openDoc: (documentId: string) => void;
  openCite: (c: KbCitation) => void;
};
export function useKbOutlet(): KbOutletCtx {
  return useOutletContext<KbOutletCtx>();
}

export function KbHome({ client = kbApi }: { client?: KbApi }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const qc = useQueryClient();
  // The citation/doc overlay is the URL (#93): any /kb/... path may carry
  // ?doc=<sourceDocId>&hl=<snippet>. Driving it from a search param makes a
  // followed citation shareable and closeable with the Back button.
  const [searchParams, setSearchParams] = useSearchParams();
  const docId = searchParams.get("doc");
  const snippet = searchParams.get("hl") ?? undefined;

  const onChats = pathname.startsWith("/kb/chats");
  // Set/clear the overlay params, preserving the rest of the URL (e.g. the
  // grid's ?view=). URLSearchParams percent-encodes the opaque id for us.
  const setDoc = (id: string | null, hl?: string) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id) {
        next.set("doc", id);
        if (hl) next.set("hl", hl);
        else next.delete("hl");
      } else {
        next.delete("doc");
        next.delete("hl");
      }
      return next;
    });
  const openCite = (c: KbCitation) => setDoc(c.document_id, c.snippet);
  const openDoc = (documentId: string) => setDoc(documentId);

  return (
    <div className="kb-shell">
      <aside className="kb-nav">
        <div className="kb-nav__brand">Knowledge base</div>
        <button
          type="button"
          className={`kb-nav__item${onChats ? "" : " is-active"}`}
          onClick={() => navigate("/kb/collections")}
        >
          <Icon name="layers" size={15} /> Collections
        </button>
        <button
          type="button"
          className={`kb-nav__item${onChats ? " is-active" : ""}`}
          onClick={() => navigate("/kb/chats")}
        >
          <Icon name="chat" size={15} /> Chats
        </button>
        <button type="button" className="kb-nav__back" onClick={() => navigate("/")}>
          <Icon name="chev_l" size={13} /> Investigations
        </button>
      </aside>

      <main className="kb-main">
        <header className="kb-topbar">
          <span className="kb-topbar__title">{onChats ? "Conversations" : "Collections"}</span>
          <HealthDot />
          {/* Same component Home uses — routes our own surface on
              manage/history and reuses our viewer for citations. */}
          <AskAgentLauncher
            client={client}
            onManage={() => navigate("/kb/collections")}
            onHistory={() => navigate("/kb/chats")}
            onOpenCitation={openCite}
          />
        </header>

        <div className="kb-surface">
          <Outlet context={{ openDoc, openCite } satisfies KbOutletCtx} />
        </div>
      </main>

      {docId && (
        <KbDocViewer
          documentId={docId}
          snippet={snippet}
          onClose={() => setDoc(null)}
          onChanged={() => void qc.invalidateQueries({ queryKey: qk.kb.all })}
          client={client}
        />
      )}
    </div>
  );
}

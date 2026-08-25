/**
 * The Knowledge-base route subtree — the App's single source of truth for
 * `/kb/*`. Exported as a function so production (`App.tsx`) mounts it with the
 * real `kbApi`, while tests mount it with the in-memory mock client. Every KB
 * view is URL-addressable (#93); the shell (KbHome) frames the matched child.
 */

import { Navigate, Route, useLocation, useSearchParams } from "react-router-dom";

import { kbApi, type KbApi } from "../../api/kb";
import { CardsTab, DocumentsTab, KbCollectionPage, ReviewTab, WikiTab } from "./KbCollectionPage";
import { KbChatsSurface } from "./KbChatsSurface";
import { KbCollectionsGrid } from "./KbCollectionsGrid";
import { GraphBrowsePage } from "./GraphBrowsePage";
import { GraphEntityPage } from "./GraphEntityPage";
import { KbHome } from "./KbHome";

/** /kb is not a page of its own — land on the collections grid. Honours the
 * legacy `/kb?tab=chats` deep-link by bouncing it to the chats surface. */
function KbIndexRedirect() {
  const [sp] = useSearchParams();
  return <Navigate to={sp.get("tab") === "chats" ? "/kb/chats" : "/kb/collections"} replace />;
}

/** The bare collection path lands on Documents.
 *
 * As its own component rather than an inline `<Navigate to="documents">`,
 * because a relative `<Navigate>` drops the SEARCH string: a query param set
 * just before the redirect — #715 puts the running import's id there so the
 * progress survives the jump from the landing page — would be gone on arrival,
 * and nothing would say why. Any future param hits the same wall, so the fix
 * belongs to the redirect, not to the one caller that noticed. */
function DefaultTabRedirect() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: "documents", search }} replace />;
}

export function kbRoutes(client: KbApi = kbApi) {
  return (
    <Route path="/kb" element={<KbHome client={client} />}>
      <Route index element={<KbIndexRedirect />} />
      <Route path="collections" element={<KbCollectionsGrid client={client} />} />
      {/* An open collection frames a tab (documents / cards / wiki) via Outlet;
          the bare path lands on Documents. */}
      <Route path="collections/:cid" element={<KbCollectionPage client={client} />}>
        <Route index element={<DefaultTabRedirect />} />
        {/* The leaf (open doc / card / wiki page) is the URL too (#93): a splat
            for the slash-bearing file paths, a plain segment for the card id. */}
        <Route path="documents/*" element={<DocumentsTab />} />
        <Route path="cards" element={<CardsTab />} />
        <Route path="cards/:cardId" element={<CardsTab />} />
        <Route path="wiki/*" element={<WikiTab />} />
        <Route path="review" element={<ReviewTab />} />
      </Route>
      {/* The open conversation is the URL too (#93). `:chatId === "new"` is the
          unsaved composer; both paths render the same surface so the new→real-id
          transition is a param change (no remount, keeps the live stream). */}
      {/* #636: the graph browser needs no collection, so it is a surface of the
          knowledge base itself — inside the shell, beside collections and chats,
          rather than a page you fall out of the KB into. */}
      <Route path="graph" element={<GraphBrowsePage />} />
      <Route path="graph/entities/:entityId" element={<GraphEntityPage />} />
      <Route path="chats" element={<KbChatsSurface client={client} />} />
      <Route path="chats/:chatId" element={<KbChatsSurface client={client} />} />
    </Route>
  );
}

/**
 * The Knowledge-base route subtree — the App's single source of truth for
 * `/kb/*`. Exported as a function so production (`App.tsx`) mounts it with the
 * real `kbApi`, while tests mount it with the in-memory mock client. Every KB
 * view is URL-addressable (#93); the shell (KbHome) frames the matched child.
 */

import { Navigate, Route, useSearchParams } from "react-router-dom";

import { kbApi, type KbApi } from "../../api/kb";
import { CardsTab, DocumentsTab, KbCollectionPage, WikiTab } from "./KbCollectionPage";
import { KbChatsSurface } from "./KbChatsSurface";
import { KbCollectionsGrid } from "./KbCollectionsGrid";
import { KbHome } from "./KbHome";

/** /kb is not a page of its own — land on the collections grid. Honours the
 * legacy `/kb?tab=chats` deep-link by bouncing it to the chats surface. */
function KbIndexRedirect() {
  const [sp] = useSearchParams();
  return <Navigate to={sp.get("tab") === "chats" ? "/kb/chats" : "/kb/collections"} replace />;
}

export function kbRoutes(client: KbApi = kbApi) {
  return (
    <Route path="/kb" element={<KbHome client={client} />}>
      <Route index element={<KbIndexRedirect />} />
      <Route path="collections" element={<KbCollectionsGrid client={client} />} />
      {/* An open collection frames a tab (documents / cards / wiki) via Outlet;
          the bare path lands on Documents. */}
      <Route path="collections/:cid" element={<KbCollectionPage client={client} />}>
        <Route index element={<Navigate to="documents" replace />} />
        {/* The leaf (open doc / card / wiki page) is the URL too (#93): a splat
            for the slash-bearing file paths, a plain segment for the card id. */}
        <Route path="documents/*" element={<DocumentsTab />} />
        <Route path="cards" element={<CardsTab />} />
        <Route path="cards/:cardId" element={<CardsTab />} />
        <Route path="wiki/*" element={<WikiTab />} />
      </Route>
      {/* The open conversation is the URL too (#93). `:chatId === "new"` is the
          unsaved composer; both paths render the same surface so the new→real-id
          transition is a param change (no remount, keeps the live stream). */}
      <Route path="chats" element={<KbChatsSurface client={client} />} />
      <Route path="chats/:chatId" element={<KbChatsSurface client={client} />} />
    </Route>
  );
}

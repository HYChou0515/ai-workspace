/**
 * Context Cards tab (#106) — author a collection's lightweight glossary. Left:
 * a compact, scrollable search + card list + New. Center: the selected card as
 * a rendered markdown **preview** by default, with an Edit toggle into the
 * title / terms (keys) chips / markdown editor. Saving routes to the create /
 * update custom action (the server derives the lookup keys); the FE never
 * sends them. A new card opens straight into Edit.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { CardAttachments } from "./CardAttachments";
import ReactMarkdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbContextCard } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { MonacoEditor } from "../../components/MonacoEditor";
import { Skeleton } from "../../components/Skeleton";
import { useT } from "../../lib/i18n";
import { AutoGenerateCards } from "./AutoGenerateCards";
import { lookupByName, scanPassage } from "./cardSearch";

type SearchMode = "name" | "text";

type Draft = {
  id: string | null;
  keys: string[];
  title: string;
  body: string;
  reference_doc_ids: string[];
};
const BLANK: Draft = { id: null, keys: [], title: "", body: "", reference_doc_ids: [] };
// URL sentinel for the unsaved new-card form (card ids are `card-…`, never this).
const NEW_CARD = "new";

const cardLabel = (c: KbContextCard) => c.title || c.keys[0] || "Untitled";

export function ContextCardsTab({
  collectionId,
  client = kbApi,
}: {
  collectionId: string;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const t = useT();
  const navigate = useNavigate();
  // The open card is the URL (#93): /kb/collections/:cid/cards/:cardId.
  const { cardId } = useParams();
  const cardsBase = `/kb/collections/${encodeURIComponent(collectionId)}/cards`;
  const { data: cards = [], isPending } = useQuery({
    queryKey: qk.kb.contextCards(collectionId),
    queryFn: () => client.listContextCards(collectionId),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState(false); // existing card → preview first; New → edit
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("name");
  const [showAutoGen, setShowAutoGen] = useState(false); // #175 自動 context card modal
  // Two ways to find a card, mirroring the backend: "name" = exact key lookup;
  // "text" = scan a pasted passage for mentioned cards. Empty query → browse all.
  const shown = !query.trim()
    ? cards
    : mode === "name"
      ? lookupByName(query, cards)
      : scanPassage(query, cards);

  const invalidate = () => qc.invalidateQueries({ queryKey: qk.kb.contextCards(collectionId) });

  // Sync the editable draft to the URL's card. Re-run only when the id or the
  // list changes (NOT on every keystroke — `draft` is the live edit buffer).
  // The URL is the single source of "which card": `/cards/new` is a blank form,
  // `/cards/:id` previews that card, bare `/cards` means nothing is open.
  // biome-ignore lint/correctness/useExhaustiveDependencies: draft is the edit buffer; depending on it would clobber edits
  useEffect(() => {
    if (cardId === NEW_CARD) {
      // Entering the new-card form (from a saved card or from nothing). An
      // in-progress blank draft (id null) is left alone so typing isn't lost.
      if (draft?.id != null || draft == null) {
        setDraft({ ...BLANK });
        setEditing(true);
      }
      return;
    }
    if (!cardId) {
      if (draft) setDraft(null);
      return;
    }
    if (cardId === draft?.id) return;
    const c = cards.find((x) => x.id === cardId);
    // Only load the content here — `editing` is owned by the click handler (and
    // stays true through a just-authored card's save), so a refetch landing
    // mid-save can't bump us out of the editor.
    if (c)
      setDraft({
        id: c.id,
        keys: c.keys,
        title: c.title,
        body: c.body,
        reference_doc_ids: c.reference_doc_ids ?? [],
      });
  }, [cardId, cards]);

  const saveMut = useMutation({
    mutationFn: async (d: Draft): Promise<string> => {
      if (d.id) {
        await client.updateContextCard(d.id, {
          keys: d.keys,
          title: d.title,
          body: d.body,
          reference_doc_ids: d.reference_doc_ids,
        });
        return d.id;
      }
      return client.createContextCard({
        collection_id: collectionId,
        keys: d.keys,
        title: d.title,
        body: d.body,
        reference_doc_ids: d.reference_doc_ids,
      });
    },
    // Promote a just-authored draft to "editing the saved card" so a second
    // Save updates it instead of creating a duplicate, and put its id in the
    // URL (a no-op for an update — same id).
    onSuccess: (id) => {
      setDraft((cur) => (cur && cur.id === null ? { ...cur, id } : cur));
      navigate(`${cardsBase}/${encodeURIComponent(id)}`);
      void invalidate();
    },
  });
  // #518 drop-to-create: upload dropped/picked files through the normal ingest
  // pipeline (VLM-described, embedded — a first-class KB doc) and link the
  // resulting doc ids onto the draft. "Drop a picture on the card and it's there."
  const [attachError, setAttachError] = useState<string | null>(null);
  const attachMut = useMutation({
    mutationFn: async (files: FileList): Promise<string[]> => {
      const ids: string[] = [];
      for (const file of Array.from(files)) {
        ids.push(...(await client.uploadDocument(collectionId, file)));
      }
      return ids;
    },
    onSuccess: (ids) => {
      setAttachError(null);
      setDraft((cur) =>
        cur
          ? {
              ...cur,
              // dedupe — a re-dropped identical file returns the same doc id
              reference_doc_ids: [...new Set([...cur.reference_doc_ids, ...ids])],
            }
          : cur,
      );
    },
    onError: (e: unknown) => setAttachError(e instanceof Error ? e.message : "upload failed"),
  });
  const attachFiles = (files: FileList) => attachMut.mutate(files);

  const deleteMut = useMutation({
    mutationFn: (id: string) => client.deleteContextCard(id),
    onSuccess: () => {
      setDraft(null);
      navigate(cardsBase);
      void invalidate();
    },
  });

  const addTerm = () => {
    const t = term.trim();
    if (t && draft && !draft.keys.includes(t)) setDraft({ ...draft, keys: [...draft.keys, t] });
    setTerm("");
  };

  return (
    <div className="kb-cards">
      <aside className="kb-cards__list">
        <div className="kb-cards__search" role="search">
          <div className="kb-cards__modes" role="tablist" aria-label="How to search">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "name"}
              className={`kb-cards__mode${mode === "name" ? " is-active" : ""}`}
              onClick={() => setMode("name")}
            >
              Name
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "text"}
              className={`kb-cards__mode${mode === "text" ? " is-active" : ""}`}
              onClick={() => setMode("text")}
            >
              In text
            </button>
          </div>
          <input
            className="kb-cards__search-input"
            aria-label="Search cards"
            placeholder={
              mode === "name" ? "Find a card by name…" : "Paste text to find cards in it…"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <button
          type="button"
          className="kb-cards__new"
          // The blank form is its own URL; the effect seeds the draft.
          onClick={() => navigate(`${cardsBase}/${NEW_CARD}`)}
        >
          <Icon name="plus" size={13} /> New card
        </button>
        <button
          type="button"
          className="kb-cards__autogen"
          onClick={() => setShowAutoGen(true)}
        >
          {t("kb.cards.autogen")}
        </button>
        {showAutoGen && (
          <AutoGenerateCards
            collectionId={collectionId}
            client={client}
            onClose={() => setShowAutoGen(false)}
          />
        )}
        <ul className="kb-cards__items">
          {isPending ? (
            <li className="kb-cards__none" aria-busy="true" data-testid="kb-cards-loading">
              <Skeleton className="kb-skel--chat-row" />
              <Skeleton className="kb-skel--chat-row" />
              <Skeleton className="kb-skel--chat-row" />
            </li>
          ) : shown.length === 0 ? (
            <li className="kb-cards__none">No cards found.</li>
          ) : (
            shown.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`kb-cards__item${(cardId ?? draft?.id) === c.id ? " is-active" : ""}`}
                  onClick={() => {
                    setEditing(false); // open as a preview by default
                    navigate(`${cardsBase}/${encodeURIComponent(c.id)}`);
                  }}
                >
                  {cardLabel(c)}
                </button>
              </li>
            ))
          )}
        </ul>
      </aside>

      <section className="kb-cards__editor">
        {draft === null ? (
          // No card open: pitch the glossary's purpose + an example when the
          // collection has none yet (#173); once cards exist just invite a pick.
          <div className="kb-cards__empty">
            {cards.length === 0 && !isPending
              ? t("kb.cards.empty.none")
              : t("kb.cards.empty.unselected")}
          </div>
        ) : (
          <>
            <div className="kb-cards__viewtoggle" role="tablist" aria-label="View mode">
              <button
                type="button"
                role="tab"
                aria-selected={!editing}
                className={`kb-cards__view${!editing ? " is-active" : ""}`}
                onClick={() => setEditing(false)}
              >
                Preview
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={editing}
                className={`kb-cards__view${editing ? " is-active" : ""}`}
                onClick={() => setEditing(true)}
              >
                Edit
              </button>
            </div>

            {editing ? (
              <>
                <input
                  className="kb-cards__title"
                  aria-label="Title"
                  placeholder="Title"
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
                <div className="kb-cards__keys">
                  {draft.keys.map((k) => (
                    <span key={k} className="kb-cards__chip">
                      {k}
                      <button
                        type="button"
                        aria-label={`Remove ${k}`}
                        onClick={() =>
                          setDraft({ ...draft, keys: draft.keys.filter((x) => x !== k) })
                        }
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  <input
                    className="kb-cards__term"
                    aria-label="Add a term"
                    placeholder="Add a term…"
                    value={term}
                    onChange={(e) => setTerm(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === ",") {
                        e.preventDefault();
                        addTerm();
                      }
                    }}
                    onBlur={addTerm}
                  />
                </div>
                <div className="kb-cards__body">
                  <MonacoEditor
                    value={draft.body}
                    onChange={(body) => setDraft({ ...draft, body })}
                    language="markdown"
                  />
                </div>
                <div className="kb-cards__attachments-field">
                  <label className="kb-cards__label">Linked documents</label>
                  <CardAttachments
                    docIds={draft.reference_doc_ids}
                    editable
                    onDetach={(docId) =>
                      setDraft({
                        ...draft,
                        reference_doc_ids: draft.reference_doc_ids.filter((x) => x !== docId),
                      })
                    }
                    onAttach={attachFiles}
                  />
                  {attachMut.isPending && (
                    <p className="kb-cards__none" data-testid="card-attach-pending">
                      Uploading…
                    </p>
                  )}
                  {attachError && (
                    <p className="kb-cards__error" role="alert">
                      {attachError}
                    </p>
                  )}
                </div>
              </>
            ) : (
              <div className="kb-cards__preview">
                <h2 className="kb-cards__preview-title">
                  {draft.title || draft.keys[0] || "Untitled"}
                </h2>
                {draft.keys.length > 0 && (
                  <div className="kb-cards__keys">
                    {draft.keys.map((k) => (
                      <span key={k} className="kb-cards__chip kb-cards__chip--ro">
                        {k}
                      </span>
                    ))}
                  </div>
                )}
                <article className="md-body kb-cards__preview-body">
                  {draft.body.trim() ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{draft.body}</ReactMarkdown>
                  ) : (
                    <p className="kb-cards__none">No explanation yet.</p>
                  )}
                </article>
                <CardAttachments docIds={draft.reference_doc_ids} editable={false} />
              </div>
            )}

            <div className="kb-cards__actions">
              {editing && (
                <button
                  type="button"
                  className="kb-cards__save"
                  disabled={saveMut.isPending}
                  onClick={() => saveMut.mutate(draft)}
                >
                  Save
                </button>
              )}
              {editing && draft.id && (
                <button
                  type="button"
                  className="kb-cards__delete"
                  onClick={() => deleteMut.mutate(draft.id as string)}
                >
                  Delete
                </button>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

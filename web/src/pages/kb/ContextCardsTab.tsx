/**
 * Context Cards tab (#106) — author a collection's lightweight glossary. Left:
 * a compact, scrollable search + card list + New. Center: the selected card as
 * a rendered markdown **preview** by default, with an Edit toggle into the
 * title / terms (keys) chips / markdown editor. Saving routes to the create /
 * update custom action (the server derives the lookup keys); the FE never
 * sends them. A new card opens straight into Edit.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { kbApi, type KbApi, type KbContextCard } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { MonacoEditor } from "../../components/MonacoEditor";
import { Skeleton } from "../../components/Skeleton";
import { lookupByName, scanPassage } from "./cardSearch";

type SearchMode = "name" | "text";

type Draft = { id: string | null; keys: string[]; title: string; body: string };
const BLANK: Draft = { id: null, keys: [], title: "", body: "" };

const cardLabel = (c: KbContextCard) => c.title || c.keys[0] || "Untitled";

export function ContextCardsTab({
  collectionId,
  client = kbApi,
}: {
  collectionId: string;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const { data: cards = [], isPending } = useQuery({
    queryKey: qk.kb.contextCards(collectionId),
    queryFn: () => client.listContextCards(collectionId),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState(false); // existing card → preview first; New → edit
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("name");
  // Two ways to find a card, mirroring the backend: "name" = exact key lookup;
  // "text" = scan a pasted passage for mentioned cards. Empty query → browse all.
  const shown = !query.trim()
    ? cards
    : mode === "name"
      ? lookupByName(query, cards)
      : scanPassage(query, cards);

  const invalidate = () => qc.invalidateQueries({ queryKey: qk.kb.contextCards(collectionId) });

  const saveMut = useMutation({
    mutationFn: async (d: Draft): Promise<string> => {
      if (d.id) {
        await client.updateContextCard(d.id, { keys: d.keys, title: d.title, body: d.body });
        return d.id;
      }
      return client.createContextCard({
        collection_id: collectionId,
        keys: d.keys,
        title: d.title,
        body: d.body,
      });
    },
    // Promote a just-authored draft to "editing the saved card" so a second
    // Save updates it instead of creating a duplicate.
    onSuccess: (id) => {
      setDraft((cur) => (cur && cur.id === null ? { ...cur, id } : cur));
      void invalidate();
    },
  });
  const deleteMut = useMutation({
    mutationFn: (id: string) => client.deleteContextCard(id),
    onSuccess: () => {
      setDraft(null);
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
          onClick={() => {
            setDraft({ ...BLANK });
            setEditing(true); // a new card opens straight into the editor
          }}
        >
          + New card
        </button>
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
                  className={`kb-cards__item${draft?.id === c.id ? " is-active" : ""}`}
                  onClick={() => {
                    setDraft({ id: c.id, keys: c.keys, title: c.title, body: c.body });
                    setEditing(false); // existing card opens as a preview by default
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
          <div className="kb-cards__empty">Select a card, or create a new one.</div>
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

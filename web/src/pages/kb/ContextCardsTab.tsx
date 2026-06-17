/**
 * Context Cards tab (#106) — author a collection's lightweight glossary. Left:
 * the card list + New. Center: the selected card's title, its terms (keys) as
 * chips, and a markdown editor for the explanation. Saving routes to the
 * create / update custom action (the server derives the lookup keys); the FE
 * never sends them.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { kbApi, type KbApi, type KbContextCard } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { MonacoEditor } from "../../components/MonacoEditor";

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
  const { data: cards = [] } = useQuery({
    queryKey: qk.kb.contextCards(collectionId),
    queryFn: () => client.listContextCards(collectionId),
  });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [term, setTerm] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: qk.kb.contextCards(collectionId) });

  const saveMut = useMutation({
    mutationFn: (d: Draft) =>
      d.id
        ? client.updateContextCard(d.id, { keys: d.keys, title: d.title, body: d.body })
        : client.createContextCard({
            collection_id: collectionId,
            keys: d.keys,
            title: d.title,
            body: d.body,
          }),
    onSuccess: () => void invalidate(),
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
        <button type="button" className="kb-cards__new" onClick={() => setDraft({ ...BLANK })}>
          + New card
        </button>
        <ul>
          {cards.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className={`kb-cards__item${draft?.id === c.id ? " is-active" : ""}`}
                onClick={() =>
                  setDraft({ id: c.id, keys: c.keys, title: c.title, body: c.body })
                }
              >
                {cardLabel(c)}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="kb-cards__editor">
        {draft === null ? (
          <div className="kb-cards__empty">Select a card, or create a new one.</div>
        ) : (
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
                    onClick={() => setDraft({ ...draft, keys: draft.keys.filter((x) => x !== k) })}
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
            <div className="kb-cards__actions">
              <button
                type="button"
                className="kb-cards__save"
                onClick={() => saveMut.mutate(draft)}
              >
                Save
              </button>
              {draft.id && (
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

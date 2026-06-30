// #328: the findability probe modal — an interactive, read-only prompt-tuning
// playground. Type a representative question, see where THIS doc's content ranks
// in the real retriever (before), edit the per-collection parser guidance, and
// preview a non-persisted re-parse of just this doc (after). Nothing is written
// until "Apply to collection" persists the tuned guidance via PATCH /collection.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { kbApi, type KbApi, type KbProbeResult, type KbProbeSide } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { pxToRem } from "../../lib/pxToRem";

export function FindabilityModal({
  collectionId,
  docId,
  docPath,
  onClose,
  client = kbApi,
}: {
  collectionId: string;
  docId: string;
  docPath: string;
  onClose: () => void;
  client?: KbApi;
}) {
  const qc = useQueryClient();
  const collectionsQ = useQuery({
    queryKey: qk.kb.collections,
    queryFn: () => client.listCollections(),
  });
  const currentGuidance =
    collectionsQ.data?.find((c) => c.resource_id === collectionId)?.parser_guidance ?? "";

  const [question, setQuestion] = useState("");
  const [guidance, setGuidance] = useState(currentGuidance);
  // Prefill (and re-sync) the editor once the collection's current guidance loads.
  useEffect(() => setGuidance(currentGuidance), [currentGuidance]);
  const [result, setResult] = useState<KbProbeResult | null>(null);

  const probeMut = useMutation({
    mutationFn: (withGuidance: boolean) =>
      client.probeFindability({
        doc_id: docId,
        question,
        guidance: withGuidance ? guidance : null,
      }),
    onSuccess: (r) => setResult(r),
  });

  const applyMut = useMutation({
    mutationFn: () => client.updateCollection(collectionId, { parser_guidance: guidance }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });

  const canProbe = question.trim().length > 0 && !probeMut.isPending;
  const dirty = guidance !== currentGuidance;

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 200,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Findability — ${docPath}`}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 720,
          maxWidth: "94vw",
          maxHeight: "88vh",
          overflow: "auto",
          background: "var(--white)",
          borderRadius: "var(--radius-card)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.22)",
          padding: 20,
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <strong style={{ fontSize: pxToRem(14) }}>Findability probe</strong>
          <span className="mono" style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
            {docPath}
          </span>
          <span style={{ flex: 1 }} />
          <button type="button" className="kb-btn" aria-label="Close" onClick={onClose}>
            Close
          </button>
        </div>

        <p style={{ fontSize: pxToRem(11.5), color: "var(--text-paper-d)", margin: 0, lineHeight: 1.45 }}>
          Type a question your users would ask, then see where this document&apos;s chunks rank in
          the real retriever. Edit the parser guidance and preview a re-parse of just this document —
          nothing is saved until you apply it to the collection.
        </p>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="caps" style={{ fontSize: pxToRem(11) }}>
            Question
          </span>
          <input
            aria-label="Question"
            value={question}
            placeholder="e.g. what is the root cause of the solder void?"
            onChange={(e) => setQuestion(e.target.value)}
            style={inputStyle}
          />
        </label>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className="kb-btn"
            disabled={!canProbe}
            onClick={() => probeMut.mutate(false)}
          >
            Check ranks
          </button>
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={!canProbe}
            onClick={() => probeMut.mutate(true)}
            title="Re-parse this document with the guidance below (runs the parser — may take a moment)"
          >
            Re-parse with guidance
          </button>
          {probeMut.isPending && (
            <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", alignSelf: "center" }}>
              running…
            </span>
          )}
          {probeMut.isError && (
            <span role="alert" style={{ fontSize: pxToRem(12), color: "var(--warn)", alignSelf: "center" }}>
              probe failed
            </span>
          )}
        </div>

        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="caps" style={{ fontSize: pxToRem(11) }}>
            Parser guidance (this collection)
          </span>
          <textarea
            aria-label="Guidance"
            value={guidance}
            placeholder="e.g. If you see a fishbone diagram, emit JSON; a table, emit Markdown."
            onChange={(e) => setGuidance(e.target.value)}
            style={{ ...inputStyle, minHeight: 88, resize: "vertical" }}
          />
        </label>

        {result && (
          <div style={{ display: "flex", gap: 12 }}>
            <ProbeColumn title="Before" side={result.before} topK={result.top_k} depth={result.depth} />
            {result.after && (
              <ProbeColumn title="After (this guidance)" side={result.after} topK={result.top_k} depth={result.depth} />
            )}
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4 }}>
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={!dirty || applyMut.isPending}
            onClick={() => applyMut.mutate()}
            title="Persist this guidance for the whole collection (affects future re-parses)"
          >
            {applyMut.isSuccess && !dirty ? "Applied" : "Apply to collection"}
          </button>
          {applyMut.isError && (
            <span role="alert" style={{ fontSize: pxToRem(12), color: "var(--warn)" }}>
              apply failed
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function ProbeColumn({
  title,
  side,
  topK,
  depth,
}: {
  title: string;
  side: KbProbeSide;
  topK: number;
  depth: number;
}) {
  return (
    <section
      aria-label={title}
      style={{
        flex: 1,
        border: "1px solid var(--paper-3)",
        borderRadius: 8,
        padding: 12,
        background: "var(--paper-2)",
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <span className="caps" style={{ fontSize: pxToRem(11) }}>
          {title}
        </span>
        <span style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)" }}>
          {side.best_rank == null ? `not in top ${depth}` : `best: #${side.best_rank}`}
        </span>
      </div>
      {side.passages.length === 0 ? (
        <p style={{ fontSize: pxToRem(12), color: "var(--text-paper-d)", margin: 0 }}>
          This document did not surface in the top {depth} for the question — a red flag to fix.
        </p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          {side.passages.map((p) => (
            <li
              key={`${p.rank}-${p.location}`}
              style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: pxToRem(12) }}
            >
              <span
                className="mono"
                style={{ fontWeight: 600, color: p.in_top_k ? "var(--accent-h)" : "var(--text-paper-d)" }}
                title={p.in_top_k ? `within the top ${topK} a user sees` : "below what a user sees"}
              >
                #{p.rank}
              </span>
              {p.location && (
                <span style={{ color: "var(--text-paper-d)" }}>{p.location}</span>
              )}
              <span style={{ color: "var(--text-paper-d)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.text}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--paper-3)",
  background: "var(--paper)",
  font: "inherit",
  fontSize: pxToRem(13),
  lineHeight: 1.5,
  boxSizing: "border-box",
};

/**
 * WikiBrowser — a collection's LLM wiki (#50 P7). The wiki is AI-maintained;
 * #D makes the page list an editable filesystem (the ready view is the shared
 * IDE shell — see KbWikiIde), while this component keeps the chrome around it:
 * the header + Rebuild / live "Updating…" indicator, the first-build progress
 * panel, the empty + error states, and the per-collection guidance editor (#90).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { KbWikiIde } from "./KbWikiIde";

// The maintainer's phases, in order — shown as the first-build step list and,
// shrunk, as the header pill's live label during a rebuild.
const WIKI_PHASES: [string, string][] = [
  ["reading", "Reading documents"],
  ["identifying", "Identifying entities & concepts"],
  ["writing", "Writing pages"],
];
const PHASE_LABEL: Record<string, string> = Object.fromEntries(WIKI_PHASES);

/**
 * Issue #90: edit the collection's per-wiki guidance — text APPENDED onto the
 * bundled wiki prompts (never a replacement). Rendered in both the empty and
 * populated states, so guidance can be set BEFORE the first build (no
 * death-lock). Saving PATCHes the collection.
 */
function WikiGuidanceEditor({
  collectionId,
  maintainerGuidance,
  readerGuidance,
  client,
}: {
  collectionId: string;
  maintainerGuidance: string;
  readerGuidance: string;
  client: KbApi;
}) {
  const qc = useQueryClient();
  const [writing, setWriting] = useState(maintainerGuidance);
  const [answering, setAnswering] = useState(readerGuidance);
  useEffect(() => {
    setWriting(maintainerGuidance);
    setAnswering(readerGuidance);
  }, [maintainerGuidance, readerGuidance]);

  const saveMut = useMutation({
    mutationFn: () =>
      client.updateCollection(collectionId, {
        wiki_maintainer_guidance: writing,
        wiki_reader_guidance: answering,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });
  const dirty = writing !== maintainerGuidance || answering !== readerGuidance;

  const ta = {
    width: "100%",
    minHeight: 64,
    resize: "vertical" as const,
    padding: "8px 10px",
    borderRadius: 8,
    border: "1px solid var(--paper-3)",
    background: "var(--paper)",
    font: "inherit",
    fontSize: 13,
    lineHeight: 1.5,
    boxSizing: "border-box" as const,
  };
  const hint = { fontSize: 11.5, color: "var(--text-paper-d)", margin: "2px 0 6px", lineHeight: 1.45 };

  return (
    <section
      aria-label="Wiki guidance"
      style={{
        textAlign: "left",
        border: "1px solid var(--paper-3)",
        borderRadius: 10,
        padding: 16,
        background: "var(--paper-2)",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        maxWidth: 560,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Icon name="sparkle" size={13} color="var(--accent-h)" />
        <span className="caps" style={{ fontSize: 11 }}>
          Wiki guidance
        </span>
      </div>

      <label htmlFor="wiki-writing-guidance" style={{ fontSize: 12.5, fontWeight: 600 }}>
        Writing guidance
      </label>
      <p style={hint}>
        How pages are organised and written. Applies to documents added from now on — rebuild to
        apply it to existing pages.
      </p>
      <textarea
        id="wiki-writing-guidance"
        value={writing}
        placeholder="e.g. Group pages by reflow zone; keep an index of defect codes."
        onChange={(e) => setWriting(e.target.value)}
        style={ta}
      />

      <label htmlFor="wiki-answering-guidance" style={{ fontSize: 12.5, fontWeight: 600, marginTop: 10 }}>
        Answering guidance
      </label>
      <p style={hint}>How questions are answered. Takes effect on the next question.</p>
      <textarea
        id="wiki-answering-guidance"
        value={answering}
        placeholder="e.g. Lead with a one-line summary, then the detail."
        onChange={(e) => setAnswering(e.target.value)}
        style={ta}
      />

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
        <button
          type="button"
          className="kb-btn kb-btn--primary"
          disabled={!dirty || saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isPending ? "Saving…" : "Save guidance"}
        </button>
        {saveMut.isError && (
          <span role="alert" style={{ fontSize: 12, color: "var(--warn)" }}>
            Couldn't save — try again.
          </span>
        )}
      </div>
    </section>
  );
}

export function WikiBrowser({
  collectionId,
  collectionName = "Collection",
  onOpenDoc,
  client = kbApi,
  maintainerGuidance = "",
  readerGuidance = "",
}: {
  collectionId: string;
  collectionName?: string;
  /** Open one of the collection's source documents (Sources footer click). */
  onOpenDoc?: (documentId: string) => void;
  client?: KbApi;
  maintainerGuidance?: string;
  readerGuidance?: string;
}) {
  const qc = useQueryClient();
  const t = useT();
  // #173: a rebuild can rewrite pages the user hand-edited, so the header
  // Rebuild button asks first instead of firing immediately.
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const { data: tree, isPending } = useQuery({
    queryKey: qk.kb.wikiPages(collectionId),
    queryFn: () => client.listWikiPages(collectionId),
  });
  // #79: `.gitkeep` only keeps an empty wiki dir alive — never a real page.
  const pages = (tree?.pages ?? []).filter((p) => !p.endsWith(".gitkeep"));

  // Live build progress — poll while a build is in flight; idle otherwise.
  const { data: status } = useQuery({
    queryKey: qk.kb.wikiStatus(collectionId),
    queryFn: () => client.getWikiStatus(collectionId),
    refetchInterval: (q) => (q.state.data?.building ? 1200 : false),
  });
  const building = status?.building ?? false;

  // When a build finishes, pull in the pages it wrote.
  const wasBuilding = useRef(false);
  useEffect(() => {
    if (wasBuilding.current && !building) {
      void qc.invalidateQueries({ queryKey: qk.kb.wikiPages(collectionId) });
    }
    wasBuilding.current = building;
  }, [building, collectionId, qc]);

  const rebuildMut = useMutation({
    mutationFn: () => client.rebuildWiki(collectionId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.wikiStatus(collectionId) });
      void qc.invalidateQueries({ queryKey: qk.kb.wikiPages(collectionId) });
    },
  });

  const header = (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 18px",
        borderBottom: "1px solid var(--paper-3)",
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          background: "var(--ink)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Icon name="layers" size={15} color="var(--accent)" />
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>
        {collectionName} <span style={{ color: "var(--text-paper-d2)", fontWeight: 400 }}>· Wiki</span>
      </div>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: "2px 8px",
          borderRadius: 4,
          background: "var(--paper-2)",
          fontSize: 11,
          color: "var(--text-paper-d)",
        }}
      >
        <Icon name="sparkle" size={11} color="var(--accent-h)" /> {t("kb.wiki.badge")}
      </span>
      <span style={{ flex: 1 }} />
      {building ? (
        <span
          data-testid="wiki-building"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "3px 9px",
            borderRadius: 4,
            background: "var(--accent-soft)",
            fontSize: 11,
            color: "var(--accent-h)",
          }}
        >
          {/* Compact, single-line progress — the full takeover's info (current
              phase + source counter) shrunk to the header, not omitted. */}
          <Icon name="refresh" size={11} /> Updating
          {status?.phase && PHASE_LABEL[status.phase] ? ` · ${PHASE_LABEL[status.phase]}` : ""}
          {(status?.total ?? 0) > 0 ? (
            <span className="mono" style={{ opacity: 0.85 }}>
              · {status?.done ?? 0} / {status?.total ?? 0}
            </span>
          ) : null}
        </span>
      ) : confirmRebuild ? (
        // #173: rebuild may rewrite hand-edited pages — confirm at the point of
        // action (inline, matching the collection page's confirm rows).
        <span
          role="dialog"
          aria-label="Confirm rebuild"
          style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}
        >
          <span style={{ fontSize: 12, color: "var(--text-paper-d)", maxWidth: 320, lineHeight: 1.45 }}>
            {t("kb.wiki.rebuild.confirm")}
          </span>
          <button
            type="button"
            className="kb-btn kb-btn--primary"
            disabled={rebuildMut.isPending}
            onClick={() => {
              setConfirmRebuild(false);
              rebuildMut.mutate();
            }}
          >
            {t("kb.wiki.rebuild.confirm.go")}
          </button>
          <button type="button" className="kb-btn" onClick={() => setConfirmRebuild(false)}>
            {t("kb.wiki.rebuild.confirm.cancel")}
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="kb-btn"
          title="Refresh the wiki from the documents"
          disabled={rebuildMut.isPending}
          onClick={() => setConfirmRebuild(true)}
        >
          <Icon name="refresh" size={13} /> Rebuild
        </button>
      )}
    </div>
  );

  // A finished build that hit errors (e.g. the maintainer ran out of steps and
  // wrote nothing) must say so — never look merely "empty".
  const errorBanner =
    !building && status && status.errors > 0 && status.last_error ? (
      <div
        role="alert"
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 8,
          padding: "10px 12px",
          borderRadius: 8,
          background: "color-mix(in srgb, var(--warn) 12%, transparent)",
          color: "var(--warn)",
          fontSize: 12.5,
          lineHeight: 1.5,
          textAlign: "left",
          maxWidth: 460,
        }}
      >
        <Icon name="flame" size={14} />
        <span>
          The last build had a problem on {status.errors} of {status.total} document
          {status.total === 1 ? "" : "s"}: {status.last_error}
        </span>
      </div>
    ) : null;

  if (isPending) {
    return (
      <p className="kb-cols__empty" role="status" aria-live="polite">
        Loading the wiki…
      </p>
    );
  }

  // ── first build: a pass is in flight and there's nothing to read yet ──
  // Once any page exists, we fall through to the editable IDE so the done pages
  // stay browsable; the build then shows only as the slim header "Updating…"
  // pill, not a full-screen takeover.
  if (building && pages.length === 0) {
    const STEPS = WIKI_PHASES;
    const activeIdx = STEPS.findIndex(([k]) => k === status?.phase);
    return (
      <div className="kb-wiki" style={{ border: "1px solid var(--paper-3)", borderRadius: 10, overflow: "hidden" }}>
        {header}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            textAlign: "center",
            gap: 16,
            padding: 40,
          }}
        >
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 12,
              background: "var(--accent-soft)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Icon name="refresh" size={22} color="var(--accent-h)" />
          </div>
          <div style={{ maxWidth: 400 }}>
            <h2 className="display" style={{ fontSize: 20, marginBottom: 6 }}>
              Updating the wiki…
            </h2>
            <p style={{ fontSize: 13.5, color: "var(--text-paper-d)", lineHeight: 1.55, margin: 0 }}>
              The assistant is reading the documents and writing pages. Pages already done stay
              readable.
            </p>
          </div>
          <div style={{ width: 280, display: "flex", flexDirection: "column", gap: 8 }}>
            {STEPS.map(([key, labelText], i) => {
              const done = activeIdx >= 0 && i < activeIdx;
              const on = i === activeIdx;
              return (
                <div
                  key={key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    fontSize: 12.5,
                    color: done || on ? "var(--text-paper)" : "var(--text-paper-d2)",
                  }}
                >
                  <span
                    style={{
                      width: 16,
                      height: 16,
                      borderRadius: "50%",
                      flexShrink: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      background: done ? "var(--ok)" : on ? "var(--accent)" : "transparent",
                      border: done || on ? 0 : "2px solid var(--paper-3)",
                    }}
                  >
                    {done && <Icon name="check" size={10} color="var(--white)" />}
                  </span>
                  <span>{labelText}</span>
                  {i === STEPS.length - 1 && (status?.total ?? 0) > 0 && (
                    <span className="mono" style={{ marginLeft: "auto", color: "var(--accent)", fontSize: 11 }}>
                      {status?.done ?? 0} / {status?.total ?? 0}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // ── empty: turned on, no pages yet ──────────────────────────────────
  if (pages.length === 0) {
    return (
      <div
        className="kb-wiki kb-wiki--empty"
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          textAlign: "center",
          gap: 14,
          padding: 40,
        }}
      >
        <div
          style={{
            width: 52,
            height: 52,
            borderRadius: 13,
            background: "var(--accent-soft)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Icon name="layers" size={24} color="var(--accent-h)" />
        </div>
        {errorBanner}
        <div style={{ maxWidth: 400 }}>
          <h2 className="display" style={{ fontSize: 20, marginBottom: 6 }}>
            The wiki hasn't been built yet
          </h2>
          <p style={{ fontSize: 13.5, color: "var(--text-paper-d)", lineHeight: 1.55, margin: 0 }}>
            This collection has the wiki turned on, but no pages have been written. Build it once and
            it keeps itself current as documents are added.
          </p>
        </div>
        <button
          type="button"
          className="kb-btn kb-btn--primary"
          disabled={rebuildMut.isPending}
          onClick={() => rebuildMut.mutate()}
        >
          <Icon name="sparkle" size={13} /> {rebuildMut.isPending ? "Building…" : "Build the wiki"}
        </button>
        <WikiGuidanceEditor
          collectionId={collectionId}
          maintainerGuidance={maintainerGuidance}
          readerGuidance={readerGuidance}
          client={client}
        />
      </div>
    );
  }

  // ── ready (possibly mid-rebuild): the editable wiki IDE ─────────────
  return (
    <div className="kb-wiki" style={{ border: "1px solid var(--paper-3)", borderRadius: 10, overflow: "hidden" }}>
      {header}
      {errorBanner && <div style={{ padding: "10px 14px 0" }}>{errorBanner}</div>}
      <KbWikiIde collectionId={collectionId} onOpenDoc={onOpenDoc} client={client} />
      <div style={{ padding: "0 14px 16px", borderTop: "1px solid var(--paper-3)", paddingTop: 16 }}>
        <WikiGuidanceEditor
          collectionId={collectionId}
          maintainerGuidance={maintainerGuidance}
          readerGuidance={readerGuidance}
          client={client}
        />
      </div>
    </div>
  );
}

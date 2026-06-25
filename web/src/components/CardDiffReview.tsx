/**
 * #205 — context-card diff review. The →collections workflow pauses at a human gate
 * having written two files: the proposed cards (`context-card.todo.md`, editable) and a
 * read-only "before" snapshot (`.readonly/context-card.current.md`). This renders a
 * "View changes" button (only when that gate produced the diff) that opens a VSCode-style
 * Monaco diff — left = current card (read-only), right = proposed (editable) — so an
 * overwrite is never blind-signed. Edits to the right pane are saved back to
 * `context-card.todo.md` before the decision commits them. Decisions reuse the gate's
 * `onDecide`, so the modal and the card stay in sync.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { type FileService, investigationFileService } from "../api/fileService";
import { useT } from "../lib/i18n";
import { MonacoDiffEditor } from "./MonacoDiffEditor";

export const TODO_PATH = "/context-card.todo.md";
export const CURRENT_PATH = "/.readonly/context-card.current.md";

const ACTION_LABEL: Record<string, string> = {
  approve: "Approve",
  reject: "Reject",
  revise: "Revise",
};

export function CardDiffReview({
  slug,
  itemId,
  allow,
  busy,
  onDecide,
  service,
}: {
  slug: string;
  itemId: string;
  allow: string[];
  busy?: boolean;
  onDecide: (choice: string, input?: string) => void;
  service?: FileService; // injectable for tests; defaults to the item's file service
}) {
  const t = useT();
  const svc = useMemo(
    () => service ?? investigationFileService(slug, itemId),
    [service, slug, itemId],
  );
  const [open, setOpen] = useState(false);
  // Show the button only when this gate produced a proposed-cards file (the
  // →collections review). Other gates (e.g. →memory) have none → no button.
  const files = useQuery({
    queryKey: ["cardDiffPresence", slug, itemId],
    queryFn: () => svc.listFiles(),
  });
  const hasDiff = (files.data ?? []).some((f) => f.path === TODO_PATH);
  if (!hasDiff) return null;

  return (
    <>
      <button
        type="button"
        data-testid="card-diff-open"
        disabled={busy}
        onClick={() => setOpen(true)}
        style={{
          padding: "5px 12px",
          borderRadius: 6,
          border: "1px solid var(--accent)",
          background: "var(--accent-soft, rgba(43,108,176,.1))",
          color: "var(--accent-h, var(--accent))",
          cursor: busy ? "default" : "pointer",
          fontWeight: 600,
        }}
      >
        {t("cardDiff.view")}
      </button>
      {open && (
        <CardDiffModal
          svc={svc}
          allow={allow}
          busy={busy}
          onDecide={(c, i) => {
            setOpen(false);
            onDecide(c, i);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

function CardDiffModal({
  svc,
  allow,
  busy,
  onDecide,
  onClose,
}: {
  svc: FileService;
  allow: string[];
  busy?: boolean;
  onDecide: (choice: string, input?: string) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [loaded, setLoaded] = useState<{ current: string; todo: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [revising, setRevising] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let alive = true;
    const readText = (path: string) =>
      svc
        .readFile(path)
        .then((c) => (c.kind === "text" ? c.text : ""))
        .catch(() => "");
    Promise.all([readText(CURRENT_PATH), readText(TODO_PATH)]).then(([current, todo]) => {
      if (!alive) return;
      setLoaded({ current, todo });
      setDraft(todo);
    });
    return () => {
      alive = false;
    };
  }, [svc]);

  const decide = async (choice: string, input?: string) => {
    // Persist right-pane edits before the decision commits them (last-write-wins;
    // a failed write surfaces on the run, not here).
    if (loaded && draft !== loaded.todo) {
      await svc.writeFile(TODO_PATH, draft).catch(() => {});
    }
    onDecide(choice, input);
  };

  const onAction = (choice: string) => {
    if (choice === "revise" && !revising) {
      setRevising(true);
      return;
    }
    void decide(choice, choice === "revise" ? note : undefined);
  };

  const allNew = loaded != null && loaded.current.trim() === "";

  return (
    <div
      role="presentation"
      data-testid="card-diff-modal"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 300,
        padding: 24,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("cardDiff.title")}
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(1100px, 96vw)",
          height: "min(80vh, 760px)",
          background: "var(--white)",
          borderRadius: "var(--radius-card, 8px)",
          border: "1px solid var(--paper-3)",
          boxShadow: "0 16px 40px rgba(0,0,0,0.24)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: 14,
        }}
      >
        <header style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <strong style={{ fontSize: 14 }}>{t("cardDiff.title")}</strong>
          <span style={{ fontSize: 12, color: "var(--text-paper-d)" }}>
            ◧ {t("cardDiff.current")} · {t("cardDiff.proposed")} ◨
          </span>
        </header>
        <p style={{ margin: 0, fontSize: 12, color: "var(--text-paper-d)" }}>
          {allNew ? t("cardDiff.allNew") : t("cardDiff.hint")}
        </p>

        <div style={{ flex: 1, minHeight: 0 }}>
          {loaded == null ? (
            <div
              style={{
                height: "100%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--text-paper-d2)",
                fontSize: 12,
              }}
            >
              {t("cardDiff.loading")}
            </div>
          ) : (
            <MonacoDiffEditor
              original={loaded.current}
              modified={loaded.todo}
              language="markdown"
              onChangeModified={setDraft}
            />
          )}
        </div>

        {revising && (
          <textarea
            data-testid="card-diff-revise-input"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What should change?"
            rows={2}
            style={{ width: "100%", fontFamily: "inherit", fontSize: 12 }}
          />
        )}

        <footer style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button
            type="button"
            data-testid="card-diff-close"
            onClick={onClose}
            style={{
              padding: "5px 12px",
              borderRadius: 6,
              border: "1px solid var(--line)",
              background: "var(--paper-2)",
              cursor: "pointer",
            }}
          >
            {t("cardDiff.close")}
          </button>
          {(allow.length ? allow : ["approve", "reject"]).map((choice) => (
            <button
              key={choice}
              type="button"
              data-action={choice}
              disabled={busy}
              onClick={() => onAction(choice)}
              style={{
                padding: "5px 12px",
                borderRadius: 6,
                border: "1px solid var(--line)",
                cursor: busy ? "default" : "pointer",
                background:
                  choice === "approve"
                    ? "var(--ok)"
                    : choice === "reject"
                      ? "var(--err)"
                      : "var(--paper-2)",
                color:
                  choice === "approve" || choice === "reject" ? "#fff" : "var(--text-paper)",
                fontWeight: 500,
              }}
            >
              {ACTION_LABEL[choice] ?? choice}
            </button>
          ))}
        </footer>
      </div>
    </div>
  );
}

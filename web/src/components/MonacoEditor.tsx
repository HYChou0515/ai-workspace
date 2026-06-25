/**
 * Monaco editor wrapper. Lazy-loaded so the ~heavy editor never bloats
 * the initial bundle — the file editor / notebook cells pull it on first
 * use. Uses the npm `monaco-editor` (no CDN) with Vite-bundled workers,
 * so it works offline.
 *
 * Callers (CellEditor, MarkdownRenderer) keep their existing
 * value/onChange API; the editor is controlled, so two panes bound to
 * the same shared buffer stay in sync, and Monaco's diff-based model
 * updates keep the cursor stable on external edits.
 */

import { lazy, Suspense } from "react";

import { pxToRem } from "../lib/pxToRem";

const LazyMonaco = lazy(() => import("./MonacoEditorImpl"));

export type MonacoEditorProps = {
  value: string;
  onChange?: (next: string) => void;
  language?: string;
  readOnly?: boolean;
  minimap?: boolean;
  /** Grow to fit content (notebook cells) vs fill the container (files). */
  autoHeight?: boolean;
  minHeight?: number;
};

export function MonacoEditor(props: MonacoEditorProps) {
  return (
    <Suspense fallback={<EditorSkeleton minHeight={props.minHeight} />}>
      <LazyMonaco {...props} />
    </Suspense>
  );
}

function EditorSkeleton({ minHeight = 120 }: { minHeight?: number }) {
  return (
    <div
      style={{
        minHeight,
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-btn)",
        background: "var(--paper-2)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--text-paper-d2)",
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(12),
      }}
    >
      loading editor…
    </div>
  );
}

/** Map a file path / cell language hint to a Monaco language id. */
export function monacoLanguage(pathOrLang: string): string {
  const s = pathOrLang.toLowerCase();
  if (s.endsWith(".md") || s.endsWith(".markdown") || s === "markdown") return "markdown";
  if (s.endsWith(".py") || s === "python") return "python";
  if (s.endsWith(".json") || s.endsWith(".canvas") || s === "json") return "json";
  if (s.endsWith(".yaml") || s.endsWith(".yml") || s === "yaml") return "yaml";
  if (s.endsWith(".csv") || s.endsWith(".tsv")) return "plaintext";
  if (s.endsWith(".js") || s.endsWith(".ts")) return "typescript";
  if (s.endsWith(".sh") || s === "bash" || s === "shell") return "shell";
  return "plaintext";
}

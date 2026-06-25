/**
 * Monaco DiffEditor wrapper (#205) — lazy-loaded so the heavy editor never bloats the
 * initial bundle (only the card-diff review pulls it). VSCode-style: original (left)
 * read-only, modified (right) editable; `onChangeModified` streams the right pane's text.
 */

import { lazy, Suspense } from "react";

import { pxToRem } from "../lib/pxToRem";

const LazyMonacoDiff = lazy(() => import("./MonacoDiffEditorImpl"));

export type MonacoDiffEditorProps = {
  original: string;
  modified: string;
  language?: string;
  onChangeModified?: (next: string) => void;
};

export function MonacoDiffEditor(props: MonacoDiffEditorProps) {
  return (
    <Suspense fallback={<DiffSkeleton />}>
      <LazyMonacoDiff {...props} />
    </Suspense>
  );
}

function DiffSkeleton() {
  return (
    <div
      style={{
        height: "100%",
        minHeight: 200,
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
      loading diff…
    </div>
  );
}

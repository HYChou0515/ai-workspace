/**
 * The Monaco DiffEditor binding (#205) — split out as a lazy chunk like
 * MonacoEditorImpl. VSCode-style review: the ORIGINAL (left) is read-only and the
 * MODIFIED (right) is editable; `onChangeModified` fires with the right pane's text so
 * the caller can persist edits to `context-card.todo.md`. Workers + loader are bundled
 * (no CDN), same as MonacoEditorImpl — the setup is idempotent if both chunks load.
 */

import { DiffEditor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

import type { MonacoDiffEditorProps } from "./MonacoDiffEditor";

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};
loader.config({ monaco });

export default function MonacoDiffEditorImpl({
  original,
  modified,
  language = "markdown",
  onChangeModified,
}: MonacoDiffEditorProps) {
  const dark =
    typeof document !== "undefined" && document.documentElement.dataset.theme === "dark";
  return (
    <div
      style={{
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-btn)",
        overflow: "hidden",
        height: "100%",
        minHeight: 0,
      }}
    >
      <DiffEditor
        original={original}
        modified={modified}
        language={language}
        theme={dark ? "vs-dark" : "vs"}
        onMount={(editor) => {
          // The right (modified) pane is the editable draft; mirror its edits out so
          // the modal can save them to context-card.todo.md.
          const right = editor.getModifiedEditor();
          right.onDidChangeModelContent(() => onChangeModified?.(right.getValue()));
        }}
        options={{
          // Left pane read-only, right pane editable — VSCode's diff default.
          originalEditable: false,
          readOnly: false,
          renderSideBySide: true,
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: "var(--font-mono)",
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
          wordWrap: "on",
          padding: { top: 8, bottom: 8 },
          scrollbar: { alwaysConsumeMouseWheel: false },
        }}
      />
    </div>
  );
}

/**
 * The actual Monaco binding — split out so it's a lazy chunk. Configures
 * the npm monaco + Vite-bundled web workers (no CDN), then renders a
 * controlled <Editor>.
 */

import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import { useState } from "react";

import type { MonacoEditorProps } from "./MonacoEditor";

// Route Monaco's worker requests to the Vite-bundled worker chunks.
self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};

// Use the bundled monaco rather than the default jsDelivr CDN copy.
loader.config({ monaco });

export default function MonacoEditorImpl({
  value,
  onChange,
  language = "plaintext",
  readOnly = false,
  minimap = false,
  autoHeight = false,
  minHeight = 120,
}: MonacoEditorProps) {
  const [height, setHeight] = useState(minHeight);
  const dark =
    typeof document !== "undefined" && document.documentElement.dataset.theme === "dark";

  return (
    <div
      style={{
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-btn)",
        overflow: "hidden",
        height: autoHeight ? height : "100%",
        minHeight,
      }}
    >
      <Editor
        value={value}
        language={language}
        theme={dark ? "vs-dark" : "vs"}
        onChange={(v) => onChange?.(v ?? "")}
        onMount={(editor) => {
          if (!autoHeight) return;
          const apply = () =>
            setHeight(Math.max(minHeight, editor.getContentHeight() + 8));
          apply();
          editor.onDidContentSizeChange(apply);
        }}
        options={{
          readOnly,
          minimap: { enabled: minimap },
          fontSize: 13,
          fontFamily: "var(--font-mono)",
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          automaticLayout: true,
          wordWrap: language === "markdown" ? "on" : "off",
          renderLineHighlight: "line",
          tabSize: 2,
          padding: { top: 8, bottom: 8 },
          scrollbar: { alwaysConsumeMouseWheel: false },
        }}
      />
    </div>
  );
}

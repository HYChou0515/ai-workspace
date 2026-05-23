/**
 * F9 — output renderer. Walks the cell's outputs array; for each output
 * picks the richest mime type (image/png > text/html > text/plain) and
 * renders accordingly. HTML is sanitized via dompurify (pandas tables are
 * the main motivation). ANSI escapes are stripped from tracebacks.
 */

import DOMPurify from "dompurify";

import { type NbOutput, pickMime } from "./types";

export function CellOutput({ outputs }: { outputs: NbOutput[] }) {
  if (outputs.length === 0) return null;
  return (
    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
      {outputs.map((o, i) => (
        <OutputItem key={i} output={o} />
      ))}
    </div>
  );
}

function OutputItem({ output }: { output: NbOutput }) {
  switch (output.output_type) {
    case "stream":
      return (
        <pre
          style={{
            margin: 0,
            padding: "6px 10px",
            background: "var(--paper-2)",
            borderRadius: 4,
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: output.name === "stderr" ? "var(--err)" : "var(--text-paper)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {Array.isArray(output.text) ? output.text.join("") : output.text}
        </pre>
      );
    case "display_data":
    case "execute_result": {
      const picked = pickMime(output.data);
      if (!picked) return null;
      if (picked.mime === "image/png") {
        return (
          <img
            alt=""
            src={`data:image/png;base64,${picked.body.trim()}`}
            style={{ maxWidth: "100%", borderRadius: 4 }}
          />
        );
      }
      if (picked.mime === "text/html") {
        const sanitized = DOMPurify.sanitize(picked.body);
        return (
          <div
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: sanitized }}
            style={{
              fontSize: 13,
              maxWidth: "100%",
              overflowX: "auto",
            }}
          />
        );
      }
      return (
        <pre
          style={{
            margin: 0,
            padding: "6px 10px",
            background: "var(--paper-2)",
            borderRadius: 4,
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--text-paper)",
            whiteSpace: "pre-wrap",
          }}
        >
          {picked.body}
        </pre>
      );
    }
    case "error":
      return (
        <pre
          style={{
            margin: 0,
            padding: "8px 12px",
            background: "rgba(196,74,58,.06)",
            border: "1px solid var(--err)",
            borderRadius: 4,
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--err)",
            whiteSpace: "pre-wrap",
          }}
        >
          {output.ename}: {output.evalue}
          {"\n"}
          {output.traceback.map(stripAnsi).join("\n")}
        </pre>
      );
  }
}

function stripAnsi(s: string): string {
  // Strip CSI/SGR escapes — color codes used by ipython tracebacks. The
  // visible structure is preserved; we just drop the colour metadata.
  // eslint-disable-next-line no-control-regex
  return s.replace(/\x1b\[[0-9;]*m/g, "");
}

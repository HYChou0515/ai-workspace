/**
 * JsonlView — JSON Lines / NDJSON as one collapsible record per line (#361).
 * Each line is an independent record by format definition, so a malformed line
 * degrades to raw text on its own card without dropping its siblings. Records
 * are collapsed by default and capped to keep the DOM light.
 */

import { JsonTree } from "./JsonTree";
import { pxToRem } from "../lib/pxToRem";
import { RawText } from "./rawFallback";

const DEFAULT_MAX_RECORDS = 500;

/** Non-empty lines as `(1-based source line no, line)`. Blank lines are skipped
 * but still count, so a record's label matches its line in the raw file. */
function jsonlRecords(text: string): Array<{ lineno: number; line: string }> {
  return text
    .split(/\r?\n/)
    .map((line, i) => ({ lineno: i + 1, line }))
    .filter((r) => r.line.trim() !== "");
}

function Record({ line }: { line: string }) {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    return <RawText text={line} note="Couldn't parse this line as JSON — showing raw text." />;
  }
  return <JsonTree value={value} collapsed />;
}

export function JsonlView({
  text,
  maxRecords = DEFAULT_MAX_RECORDS,
}: {
  text: string;
  maxRecords?: number;
}) {
  const recs = jsonlRecords(text);
  if (recs.length === 0) return <div style={{ color: "var(--text-paper-d)" }}>Empty file.</div>;
  const shown = recs.slice(0, maxRecords);
  const capped = recs.length - shown.length;

  return (
    <div style={{ height: "100%", minHeight: 0, overflow: "auto" }}>
      {shown.map(({ lineno, line }) => (
        <div
          key={lineno}
          data-testid="jsonl-record"
          className="jsonl-record"
          style={{ display: "flex", gap: 8, alignItems: "flex-start", borderBottom: "1px solid var(--rule)" }}
        >
          <span
            className="jsonl-record__no"
            style={{ color: "var(--text-paper-d)", fontSize: pxToRem(11), padding: "6px 0", minWidth: pxToRem(28), textAlign: "right", userSelect: "none" }}
          >
            {lineno}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Record line={line} />
          </div>
        </div>
      ))}
      {capped > 0 && (
        <div style={{ color: "var(--text-paper-d)", fontSize: pxToRem(12), padding: "6px 2px" }}>
          {recs.length} records — showing first {maxRecords} (Edit to see all)
        </div>
      )}
    </div>
  );
}

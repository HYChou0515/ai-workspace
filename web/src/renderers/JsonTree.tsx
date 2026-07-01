/**
 * JsonTree — the ONE place we touch react18-json-view (#361). Every structured
 * renderer that shows a collapsible tree (JSON, YAML, each JSONL record) goes
 * through here, so the library, its CSS, and our default look are single-sourced.
 * Read-only: editing a structured file flips to the byte editor via the registry
 * editToggle, so the tree never mutates the doc.
 */

import JsonView from "react18-json-view";
import "react18-json-view/src/style.css";

import { pxToRem } from "../lib/pxToRem";

export function JsonTree({
  value,
  collapsed = 1,
}: {
  value: unknown;
  /** collapse nodes deeper than this depth; `true` collapses the root. */
  collapsed?: number | boolean;
}) {
  return (
    <div className="json-tree" style={{ fontSize: pxToRem(12), padding: "4px 2px" }}>
      <JsonView src={value} collapsed={collapsed} enableClipboard />
    </div>
  );
}

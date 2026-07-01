/**
 * JsonTreeView — raw JSON text → a collapsible tree (#361). A pure text→view
 * core reused by the workspace JSON renderer and the KB read-only doc viewer.
 * Oversized or malformed input degrades to the verbatim raw text (never a
 * crash / frozen tab) — see rawFallback.
 */

import { JsonTree } from "./JsonTree";
import { byteLength, RawText } from "./rawFallback";

// react18-json-view renders every node eagerly; a multi-MB blob janks the tab.
// Above the cap we show raw text — Download / Edit is the full-fidelity path.
const DEFAULT_MAX_BYTES = 2_000_000;

export function JsonTreeView({
  text,
  maxBytes = DEFAULT_MAX_BYTES,
  collapsed = 1,
}: {
  text: string;
  maxBytes?: number;
  collapsed?: number | boolean;
}) {
  if (byteLength(text) > maxBytes) {
    return <RawText text={text} note="File is large — showing raw text (Download for the full view)." />;
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return <RawText text={text} note="Couldn't parse as JSON — showing raw text." />;
  }
  return <JsonTree value={value} collapsed={collapsed} />;
}

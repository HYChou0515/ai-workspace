/**
 * YamlTree — raw YAML text → the same collapsible tree as JSON (#361). YAML is
 * a JSON superset structurally, so once parsed it rides the shared JsonTree.
 * Oversized or malformed input degrades to verbatim raw text.
 */

import { load as parseYaml } from "js-yaml";

import { JsonTree } from "./JsonTree";
import { byteLength, RawText } from "./rawFallback";

const DEFAULT_MAX_BYTES = 2_000_000;

export function YamlTree({
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
    value = parseYaml(text);
  } catch {
    return <RawText text={text} note="Couldn't parse as YAML — showing raw text." />;
  }
  return <JsonTree value={value} collapsed={collapsed} />;
}

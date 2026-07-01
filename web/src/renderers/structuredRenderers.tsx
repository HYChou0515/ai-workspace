/**
 * The FileService-bound structured renderers (#361): thin adapters that read the
 * file text through the shared buffer and hand it to a pure presentational core
 * (JsonTreeView / JsonlView / YamlTree). The cores are also fed raw text by the
 * KB read-only doc viewer, so the two surfaces render identically.
 */

import { JsonlView } from "./JsonlView";
import { JsonTreeView } from "./JsonTreeView";
import { StructuredPane } from "./structuredPane";
import { YamlTree } from "./YamlTree";

export function JsonRenderer({ path }: { path: string }) {
  return <StructuredPane path={path} render={(text) => <JsonTreeView text={text} />} />;
}

export function JsonlRenderer({ path }: { path: string }) {
  return <StructuredPane path={path} render={(text) => <JsonlView text={text} />} />;
}

export function YamlRenderer({ path }: { path: string }) {
  return <StructuredPane path={path} render={(text) => <YamlTree text={text} />} />;
}

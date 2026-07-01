/**
 * Preview a CSV/TSV as a table (issue #19, #361). Reads the file text through
 * the shared buffer, parses it, and hands the rows to the pure DataGrid core
 * (the same grid the KB read-only viewer uses). Edit flips to the byte editor
 * like every other file, so it stays #all-editable.
 */

import { DataGrid } from "./DataGrid";
import { parseCsv } from "./csv";
import { StructuredPane } from "./structuredPane";

export function CsvRenderer({ path }: { path: string }) {
  // .tsv is tab-separated; everything else routed here (.csv) is comma-separated.
  // The backend parser (kb/parsers/tabular.py) splits the same way.
  const delimiter = path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
  return <StructuredPane path={path} render={(text) => <DataGrid rows={parseCsv(text, delimiter)} />} />;
}

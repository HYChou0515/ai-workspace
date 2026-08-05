/**
 * `view: csv-table` — the worked example a maintainer copies (#698).
 *
 * It is deliberately the NON-entity shape, because that is the path this repo
 * opened: it declares no `entity:`, so nothing about the item's entity types
 * matters, and it gets its data by reading a workspace file its own view file
 * names. An app with no `.entity/` at all (rca) can use it as-is.
 *
 * The view file that drives it:
 *
 *     view: csv-table
 *     title: Wafer yield
 *     source: /data/wafer.csv
 *
 * `source` is not a key the platform knows — unknown top-level keys ride
 * through `parseViewSpec` verbatim onto `spec`, typed `unknown`, which is how a
 * plug-in reads its own config.
 *
 * Everything here comes from `renderers/entity/public` — see that module for
 * why, and `docs/view-kind-authoring.md` for the guide this file mirrors.
 */

import { DataGrid, type EntityViewProps, parseCsv, useFileBuffer } from "../renderers/entity/public";

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div role="status" style={{ padding: 12, color: "var(--text-paper-d)" }}>
      {children}
    </div>
  );
}

/** Reads the file. Split out so the `source`-missing case can return early in
 * the PARENT without a conditional hook — `useFileBuffer` must run on every
 * render of the component that owns it. */
function CsvFromFile({ path }: { path: string }) {
  const { entry } = useFileBuffer(path);
  if (entry.status === "loading") return <Notice>Loading {path}…</Notice>;
  if (entry.status === "error") return <Notice>{entry.error ?? `could not read ${path}`}</Notice>;
  // Tab-separated files are as common as comma ones in exported lab data.
  const delimiter = path.toLowerCase().endsWith(".tsv") ? "\t" : ",";
  return <DataGrid rows={parseCsv(entry.text, delimiter)} />;
}

export function CsvTableView({ spec }: EntityViewProps) {
  const source = typeof spec.source === "string" ? spec.source.trim() : "";
  if (!source) {
    return <Notice>This view needs a `source:` naming the CSV/TSV file to show.</Notice>;
  }
  return <CsvFromFile path={source} />;
}

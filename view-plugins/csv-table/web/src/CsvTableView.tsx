/**
 * `view: csv-table` — the worked example a plugin author copies (#698; a
 * runtime plugin since #847/#848).
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
 * `source` is not a key the platform knows, so it is NOT on the `ViewSpec` type
 * — `spec.source` doesn't compile. Read your own keys with `viewParam` /
 * `viewParamString`, which hand back the original document (so a key of yours
 * that collides with a platform one still reads the way you wrote it).
 *
 * Everything here comes from `@aiws/view-sdk` — the host's public barrel,
 * reached through the import map — see `docs/view-kind-authoring.md` for the
 * guide this file mirrors.
 */

import type { ReactNode } from "react";

import { DataGrid, type EntityViewProps, parseCsv, useFileBuffer, viewParamString } from "@aiws/view-sdk";

function Notice({ children }: { children: ReactNode }) {
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
  // `source` is this kind's own key, so it isn't on `ViewSpec` — read it with
  // `viewParamString`, which hands back a string or nothing.
  const source = viewParamString(spec, "source")?.trim() ?? "";
  if (!source) {
    return <Notice>This view needs a `source:` naming the CSV/TSV file to show.</Notice>;
  }
  return <CsvFromFile path={source} />;
}

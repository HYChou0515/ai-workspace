/**
 * The csv-table runtime view plugin's entry (#847/#848 PR1 P10).
 *
 * Built into `<plugins dir>/csv-table/web/index.js`; the SPA `import()`s it
 * before its first render, and importing it registers the kind. `react` and
 * `@aiws/view-sdk` are external — the import map hands this module the host's
 * own copies.
 */
import { registerViewKind } from "@aiws/view-sdk";

import { CsvTableView } from "./CsvTableView";

registerViewKind({
  kind: "csv-table",
  Component: CsvTableView,
  // It follows a named marking (and writes it), so every csv-table view
  // carries the header's marking control (#847/#848 PR 5).
  linkable: true,
  // No `needsEntity` — this kind reads a workspace file, so a view file using
  // it declares no `entity:` and the entity props arrive empty.
});

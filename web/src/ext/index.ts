/**
 * Second-party view kinds (#698).
 *
 * This is the one file the main program imports — `main.tsx` does
 * `import "./ext";` for its side effects, BEFORE the app renders. Adding a kind
 * is a registration below plus its own file in this folder; nothing outside
 * this folder changes.
 *
 * Order matters only in that this module must run before the first render: the
 * registry is a plain module-level map, so a kind registered after a view has
 * already painted will not retroactively appear in it.
 *
 * Every shipping file in this folder — including subfolders — imports from
 * `renderers/entity/public` only; `./imports.test.ts` enforces that. Test files
 * are exempt (they mount the real container and the app's providers); see the
 * barrel's own docstring for why, and for what that costs.
 *
 * See `docs/view-kind-authoring.md`.
 */

import { registerViewKind } from "../renderers/entity/public";
import { CsvTableView } from "./CsvTableView";

registerViewKind({
  kind: "csv-table",
  Component: CsvTableView,
  // No `needsEntity` — this kind reads a workspace file, so a view file using
  // it declares no `entity:` and the entity props arrive empty.
});

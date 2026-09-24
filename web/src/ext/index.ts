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

// Registers nothing in this repo since #847/#848: `csv-table`, the example that
// lived here, is a RUNTIME plugin now (`view-plugins/csv-table/`). The channel
// stays — a build that carries its own second-party kinds (EE does) adds its
// `registerViewKind` calls here, importing `../renderers/entity/public` only.
export {};

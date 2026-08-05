/**
 * The surface a second-party view kind may use (#698).
 *
 * Everything under `web/src/ext/` imports from HERE and nowhere else — the
 * `no-restricted-imports` rule in `eslint.config.js` makes that a lint error
 * rather than a convention people remember. Nothing else in the app should
 * import this module; it exists to name a boundary, not to be a second way to
 * reach the same code.
 *
 * This is NOT a frozen API and carries no version. Plug-ins live in this repo
 * and compile in the same CI run, so when we change something here the breakage
 * shows up as a red build, not as a broken screen for someone's users. The
 * point of the barrel is that the blast radius of a change is *visible* at the
 * moment you make it.
 *
 * What a plug-in gets:
 *   - `registerViewKind` — how a kind joins the registry (see `ext/index.ts`)
 *   - the file seam — read ANY workspace file, which is where a plug-in's data
 *     comes from; it does not have to be entity-bound
 *   - the entity props — populated only when the kind declares `needsEntity`
 *   - small presentation helpers, so the common cases stay short
 */

// ── registration ───────────────────────────────────────────────────────────
export { registerViewKind, unregisterViewKind, viewKindNames } from "./viewKindRegistry";
export type { ViewRenderer } from "./viewKindRegistry";

// ── the view file ──────────────────────────────────────────────────────────
// `ViewSpec` carries the parsed `.ai.yaml`. Your own top-level keys survive
// verbatim (typed `unknown` — narrow them, don't cast to `any`).
export type { EntityViewProps, ViewSpec } from "./types";
export { BUILTIN_VIEW_KINDS } from "./types";

// ── workspace files: where a plug-in's data comes from ─────────────────────
// `useFileBuffer(path)` is the cached read (it also tracks external writes);
// `useFileService()` is the whole surface — listFiles / readFile / writeFile /
// fileUrl — plus `caps`, which says what THIS surface supports.
//
// `caps` is not permission. Whether this member may write the item is
// `canWrite` on your props; the server is what actually enforces it. Draw your
// write affordances off `canWrite`, or you will render a button that 403s.
export { useFileService } from "../../api/fileService";
export type { FileCaps, FileService } from "../../api/fileService";
export { useFileBuffer } from "../../hooks/fileBuffer";
export type { BufferEntry } from "../../hooks/fileBuffer";

// ── presentation helpers ───────────────────────────────────────────────────
export { fieldText, parseSpan, roleOf } from "./shared";
export { DataGrid } from "../DataGrid";
export { parseCsv } from "../csv";

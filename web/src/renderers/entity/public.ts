/**
 * The surface a second-party view kind may use (#698).
 *
 * Everything under `web/src/ext/` imports from HERE and nowhere else, enforced
 * by `ext/imports.test.ts` (this project has no ESLint) — a red build rather
 * than a convention people remember. Nothing else in the app should import this
 * module; it exists to name a boundary, not to be a second way to reach the
 * same code.
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
// `unregisterViewKind` is deliberately absent: it is a test seam, and exporting
// it here would make the duplicate-name check opt-out for exactly the code it
// exists to guard.
export { registerViewKind } from "./viewKindRegistry";
export type { ViewRenderer } from "./viewKindRegistry";

// ── the view file ──────────────────────────────────────────────────────────
// `ViewSpec` carries the parsed `.ai.yaml`. Fields the platform knows are typed
// and coerced; YOUR OWN keys are not on the type — read them with
// `viewParamString(spec, "source")` / `viewParam`, which hand back the ORIGINAL
// document, so a key of yours that happens to share a name with a platform one
// (`columns`, `card`, `sort`, `label`, `span`, `title`, `group_by`, `week`, …)
// still reads back the way you wrote it.
export type { EntityViewProps, ViewSpec } from "./types";
export { viewParam, viewParamString } from "./shared";

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
// The shapes those methods return. Without these a plug-in can call `listFiles`
// / `readFile` on inference but cannot write a typed signature over the result
// — and the only other spelling, `../../api/types`, is a boundary violation.
export type { FileContent, FileInfo } from "../../api/types";
export { useFileBuffer } from "../../hooks/fileBuffer";
export type { BufferEntry } from "../../hooks/fileBuffer";

// ── presentation helpers ───────────────────────────────────────────────────
export { fieldText, parseSpan, roleOf } from "./shared";
export { DataGrid } from "../DataGrid";
export { parseCsv } from "../csv";

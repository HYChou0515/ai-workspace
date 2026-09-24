/**
 * The surface a second-party view kind may use (#698).
 *
 * Every SHIPPING file under `web/src/ext/` imports from HERE and nowhere else,
 * enforced by `ext/imports.test.ts` (this project has no ESLint) — a red build
 * rather than a convention people remember. Nothing else in the app should
 * import this module; it exists to name a boundary, not to be a second way to
 * reach the same code.
 *
 * `*.test.tsx` files under `ext/` are EXEMPT and reach internals freely: a test
 * has to mount the real container and the app's providers, and routing that
 * scaffolding through this barrel would export test-only machinery to plug-in
 * authors as if it were product API. The cost is that a plug-in's TEST-time
 * coupling stays invisible here — break one of those internals and the plug-in
 * author fixes their own test, with CI telling them, rather than this module
 * having warned anyone in advance.
 *
 * For `ext/` (built-time plug-ins in this repo) the breakage of a change here
 * shows up as a red build. For RUNTIME plugins (#847/#848) it does not — see
 * `SDK_VERSION` below: this barrel is also `@aiws/view-sdk`, so it is versioned.
 * Either way the point of the barrel is that the blast radius of a change is
 * *visible* at the moment you make it.
 *
 * What a plug-in gets:
 *   - `registerViewKind` — how a kind joins the registry (see `ext/index.ts`)
 *   - the file seam — read ANY workspace file, which is where a plug-in's data
 *     comes from; it does not have to be entity-bound
 *   - the entity props — populated only when the kind declares `needsEntity`
 *   - small presentation helpers, so the common cases stay short
 */

// ── the runtime-plugin SDK (#847/#848) ─────────────────────────────────────
// This barrel IS `@aiws/view-sdk`: a runtime plugin (built outside this repo,
// loaded from the operator's plugin dir) imports it by that name, and the
// import map points the name at the host's own copy of this module. So unlike
// `ext/`, a runtime plugin is compiled against a SNAPSHOT of this surface —
// removing or changing an export here breaks already-built plugins at runtime,
// not at compile time. Such a change bumps `SDK_VERSION`'s major, and the
// loader then refuses the old plugins per panel, loudly.
export { SDK_VERSION } from "../../viewPlugins/sdkVersion";
export { useSandboxRun } from "../../viewPlugins/useSandboxRun";
export type { SandboxRun, SandboxRunArgs, SandboxRunResult } from "../../viewPlugins/useSandboxRun";

// ── named markings: linked selection across views (#847 PR 3) ─────────────
// `useMarking(name)` reads and writes one item-wide marking (`column → set of
// values`, opaque strings); only views on the same name link. `isLit` is THE
// matching rule — use it rather than your own, or two views on one marking
// will disagree about which rows are lit. `projectOntoKeys` turns selected
// rows into what a view writes (`null` for a view without `keys:`). Additive
// to SDK 1: no major bump.
export { useMarking } from "../../hooks/useMarking";
export type { WriteMarking } from "../../hooks/useMarking";
export { isLit, projectOntoKeys } from "../../lib/markings";
export type { Marking, MarkingEntry } from "../../lib/markings";

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
export { viewDocument, viewParam, viewParamString } from "./shared";

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

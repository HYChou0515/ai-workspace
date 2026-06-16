/**
 * The file-preview registry — the ONE place to add a preview type.
 *
 * To add a type (incl. a company-internal one): append an entry below with the
 * matching extension(s) and its renderer Component. Set:
 *   - editToggle: it has a preview ⇄ edit (byte editor) duality (md, image, …)
 *   - rawEditor:  it's a full-bleed code editor (text/json) — never a preview
 *   - outline:    it renders a markdown body (headings feed the Outline panel)
 * Order matters: first match wins; the last entry is the catch-all.
 *
 * Everything else (FileView dispatch, pane padding, edit toggle, outline)
 * derives from this list — no other file needs touching.
 */
import type { ComponentType } from "react";

import { CsvRenderer } from "./CsvRenderer";
import { HtmlRenderer } from "./HtmlRenderer";
import { ImageRenderer } from "./ImageRenderer";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { NotebookRenderer } from "./notebook/NotebookRenderer";
import { ReportRenderer } from "./report/ReportRenderer";
import { TextRenderer } from "./TextRenderer";

export type RendererProps = { path: string };

export type RendererDef = {
  key: string;
  match: (path: string) => boolean;
  Component: ComponentType<RendererProps>;
  editToggle?: boolean;
  rawEditor?: boolean;
  outline?: boolean;
};

/** Match by lowercased file extension. */
const ext =
  (...exts: string[]) =>
  (path: string): boolean =>
    exts.includes(path.toLowerCase().split(".").pop() ?? "");

export const RENDERERS: RendererDef[] = [
  // Report version files (basename `report.v*.md`) get a dedicated renderer,
  // not generic md. Matches anywhere in the workspace (root or step folder),
  // so the by-step organisation that the local-lab prompt recommends doesn't
  // demote the report to generic markdown. Same anchoring as `reportVersions`
  // in `report/versions.ts` — keep them in sync.
  {
    key: "report",
    match: (p) => /(?:^|\/)report\.v\d+\.md$/i.test(p),
    Component: ReportRenderer,
    outline: true,
  },
  { key: "markdown", match: ext("md", "markdown"), Component: MarkdownRenderer, editToggle: true, outline: true },
  { key: "notebook", match: ext("ipynb"), Component: NotebookRenderer },
  { key: "csv", match: ext("csv", "tsv"), Component: CsvRenderer, editToggle: true },
  { key: "html", match: ext("html", "htm"), Component: HtmlRenderer, editToggle: true },
  {
    key: "image",
    match: ext("png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"),
    Component: ImageRenderer,
    editToggle: true,
  },
  { key: "json", match: ext("json"), Component: TextRenderer, rawEditor: true },
  // Catch-all — keep last. Plain text in the byte editor (any file is editable).
  { key: "text", match: () => true, Component: TextRenderer, rawEditor: true },
];

const byKey = new Map(RENDERERS.map((d) => [d.key, d]));

function defForPath(path: string): RendererDef {
  return RENDERERS.find((d) => d.match(path)) ?? RENDERERS[RENDERERS.length - 1];
}

/** The renderer key for a path (e.g. "markdown", "image"). */
export function pickRenderer(path: string): string {
  return defForPath(path).key;
}

/** The renderer Component for a path (what FileView mounts). */
export function rendererComponent(path: string): ComponentType<RendererProps> {
  return defForPath(path).Component;
}

/** Whether the view is a full-bleed code editor (pad 0) vs a padded preview:
 * rawEditor types always are; editToggle types are while editing. */
export function isRawEditorView(key: string, editing: boolean): boolean {
  const d = byKey.get(key);
  if (!d) return true;
  if (d.rawEditor) return true;
  return d.editToggle ? editing : false;
}

/** Whether a type has a preview ⇄ edit toggle (markdown, image, csv, html, …). */
export function hasEditToggle(key: string): boolean {
  return byKey.get(key)?.editToggle ?? false;
}

/** Whether a path renders a markdown body whose headings feed the Outline. */
export function hasOutline(path: string): boolean {
  return defForPath(path).outline ?? false;
}

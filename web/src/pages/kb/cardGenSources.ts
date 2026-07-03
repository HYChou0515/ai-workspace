/**
 * Build the card-gen picker's source tree (#415). Documents and (when the
 * collection has an LLM wiki) wiki pages become ONE FileTree over two virtual
 * roots — `Documents/` and `Wiki/` — so the reviewer can pick from both in a
 * single mixed selection. The tree speaks paths; this also returns the
 * tree-path → id map the modal submits (docs by their resource id, wiki pages by
 * their `_rid` TYPE-TAGGED with `wiki:` so a same-path doc stays distinct), so
 * the selection maps back with no string surgery. `.gitkeep` placeholders are
 * never selectable.
 */

import type { KbDocument } from "../../api/kb";
import type { FileInfo } from "../../api/types";

const SLASH = "∕"; // U+2215 division slash — kb/wiki/store.py _SLASH / doc_id.py

/** Type-tag marking a submitted id as a wiki page, not a document — mirrors the
 * backend kb/card_gen_sources.py `WIKI_ID_PREFIX`. A wiki page `/P` and a doc
 * `P` encode to the SAME resource id, so the tag is what keeps the reviewer's
 * wiki pick from silently submitting the document's id instead. */
export const WIKI_ID_PREFIX = "wiki:";

const isGitkeep = (path: string) => path.split("/").pop() === ".gitkeep";

/** A wiki page's resource id — mirrors kb/wiki/store.py `_rid`: the leading-slash
 * page path appended to the (slash-free) collection id, every "/" swapped to
 * U+2215. Lets the picker submit a wiki page id mixed into the same `doc_ids`
 * list the backend already resolves. */
export function wikiPageId(collectionId: string, wikiPath: string): string {
  return (collectionId + wikiPath).replaceAll("/", SLASH);
}

export type CardGenSources = {
  /** The merged FileTree input, prefixed by virtual root. */
  files: FileInfo[];
  /** tree-path → the id to submit for that leaf. */
  ids: Map<string, string>;
};

export function buildCardGenSources(
  collectionId: string,
  docs: KbDocument[],
  wikiPaths: string[],
): CardGenSources {
  const files: FileInfo[] = [];
  const ids = new Map<string, string>();
  for (const d of docs) {
    if (isGitkeep(d.path)) continue;
    const treePath = `Documents/${d.path}`;
    files.push({ path: treePath, size: 0 });
    ids.set(treePath, d.resource_id);
  }
  for (const wikiPath of wikiPaths) {
    if (isGitkeep(wikiPath)) continue;
    // wiki paths carry a leading slash (`/index.md`) → `Wiki` + `/index.md`.
    const treePath = `Wiki${wikiPath}`;
    files.push({ path: treePath, size: 0 });
    ids.set(treePath, WIKI_ID_PREFIX + wikiPageId(collectionId, wikiPath));
  }
  return { files, ids };
}

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { parseCollectionsFile, type CollectionsFileParse } from "../components/collectionsFile";

/** Where a Hub item keeps its collection set (topic-hub §5). */
export const COLLECTIONS_PATH = "/collections.json";

/**
 * Read + parse a Topic Hub item's `collections.json` through the active
 * `FileService`, cached under `qk.itemCollections(scopeId)` (#142). A missing
 * file (404) is a normal empty selection, not an error — only a real read
 * failure surfaces as an error. The picker modal seeds its editable state from
 * this and the shell's badge shows `selectedIds.length`; saving the modal
 * invalidates this key so both refresh.
 */
export function useItemCollections(svc: FileService): UseQueryResult<CollectionsFileParse> {
  return useQuery<CollectionsFileParse>({
    queryKey: qk.itemCollections(svc.scopeId),
    queryFn: async () => {
      try {
        const content = await svc.readFile(COLLECTIONS_PATH);
        return parseCollectionsFile(content.kind === "text" ? content.text : null);
      } catch (err) {
        if ((err as { status?: number })?.status === 404) return parseCollectionsFile(null);
        throw err;
      }
    },
  });
}

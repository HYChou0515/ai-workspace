/**
 * Per-chat actions from the rail's ⋯ menu (#chat-private #1): rename (PATCH the
 * title — the diff-only patch strips permission so a rename never republishes)
 * and delete (hard delete, owner/superuser gated by the backend). Both refresh
 * the chat list.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";

export function useChatActions(slug: string, resourceRoute: string | undefined) {
  const qc = useQueryClient();
  const route = resourceRoute ?? "";

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.patchAppItemFields(route, id, { title }),
    onSuccess: (_d, { id }) => {
      void qc.invalidateQueries({ queryKey: qk.appItem(slug, id) });
      void qc.invalidateQueries({ queryKey: qk.appItems(slug) });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAppItem(route, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.appItems(slug) }),
  });

  return {
    rename: (id: string, title: string) => rename.mutate({ id, title }),
    remove: (id: string) => remove.mutate(id),
    busy: rename.isPending || remove.isPending,
  };
}

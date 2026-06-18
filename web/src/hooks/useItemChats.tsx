import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { itemChatApi, type ItemChatApi, type ItemChatSummary } from "../api/itemChats";
import { qk } from "../api/queryKeys";

/**
 * The item's chat LIST (topic-hub §3) — the multi-chat shell's tab source. Lists
 * every chat (free + workflow, default first) and exposes a mutation to open a new
 * free chat. `client` is injectable for tests.
 */
export type UseItemChats = {
  chats: ItemChatSummary[];
  isLoading: boolean;
  /** Open a new free chat; resolves to its summary. */
  createFreeChat: (title?: string) => Promise<ItemChatSummary>;
};

export function useItemChats(
  slug: string,
  itemId: string,
  client: ItemChatApi = itemChatApi,
): UseItemChats {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: qk.itemChats(slug, itemId),
    queryFn: () => client.listChats(slug, itemId),
  });
  const mutation = useMutation({
    mutationFn: (title: string = "") => client.createChat(slug, itemId, title),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.itemChats(slug, itemId) }),
  });
  return {
    chats: query.data ?? [],
    isLoading: query.isLoading,
    createFreeChat: (title = "") => mutation.mutateAsync(title),
  };
}

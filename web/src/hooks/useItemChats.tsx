import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { itemChatApi, type ItemChatApi, type ItemChatSummary } from "../api/itemChats";
import { qk } from "../api/queryKeys";

/**
 * The item's chat LIST (topic-hub §3) — the multi-chat switcher's source. Lists
 * every chat (free + workflow, most-recent first) and exposes mutations to open a
 * free chat, rename a chat, and delete a chat (#132). `client` is injectable for
 * tests; every write invalidates the list so the switcher + modal refresh.
 */
export type UseItemChats = {
  chats: ItemChatSummary[];
  isLoading: boolean;
  /** The list has been fetched since this component mounted — before that,
   * what it shows may be a cache from an earlier visit (a chat made since,
   * e.g. a schedule's, is not in it yet). */
  isFetchedAfterMount: boolean;
  /** Open a new free chat; resolves to its summary. */
  createFreeChat: (title?: string) => Promise<ItemChatSummary>;
  /** Rename a chat from the manage modal (#132). */
  renameChat: (chatId: string, title: string) => Promise<ItemChatSummary>;
  /** Delete a chat (#132) — the backend cancels a running workflow first. */
  deleteChat: (chatId: string) => Promise<void>;
};

export function useItemChats(
  slug: string,
  itemId: string,
  client: ItemChatApi = itemChatApi,
  /** `always`: fetch on mount even when the cache is fresh — for a caller that
   * must see a chat that may have been made since the list was cached. */
  refetchOnMount: "always" | undefined = undefined,
): UseItemChats {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: qk.itemChats(slug, itemId) });
  const query = useQuery({
    queryKey: qk.itemChats(slug, itemId),
    queryFn: () => client.listChats(slug, itemId),
    ...(refetchOnMount ? { refetchOnMount } : {}),
  });
  const create = useMutation({
    mutationFn: (title: string = "") => client.createChat(slug, itemId, title),
    onSuccess: invalidate,
  });
  const rename = useMutation({
    mutationFn: (v: { chatId: string; title: string }) =>
      client.renameChat(slug, itemId, v.chatId, v.title),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (chatId: string) => client.deleteChat(slug, itemId, chatId),
    onSuccess: invalidate,
  });
  return {
    chats: query.data ?? [],
    isLoading: query.isLoading,
    isFetchedAfterMount: query.isFetchedAfterMount,
    createFreeChat: (title = "") => create.mutateAsync(title),
    renameChat: (chatId, title) => rename.mutateAsync({ chatId, title }),
    deleteChat: (chatId) => remove.mutateAsync(chatId),
  };
}

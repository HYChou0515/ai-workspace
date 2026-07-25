/**
 * Create a new chat in a chat-first App and jump straight into it (#chat-private
 * #2). No create form — the item is made with sensible defaults (title "New
 * chat"; owner/profile/permission are defaulted server-side), so starting a chat
 * is one click and never distracts with fields. Rename later from the rail.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { qk } from "../api/queryKeys";

export function useCreateChat(slug: string) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: () => api.createAppItem(slug, { title: "New chat" }),
    onSuccess: ({ resource_id }) => {
      queryClient.invalidateQueries({ queryKey: qk.appItems(slug) });
      navigate(`/a/${slug}/${encodeURIComponent(resource_id)}`);
    },
  });
}

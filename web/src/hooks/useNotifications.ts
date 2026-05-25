import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { NotificationItem } from "../api/types";

type UseNotifications = {
  items: NotificationItem[];
  unread: number;
  markAllRead: () => void;
  markRead: (id: string) => void;
};

/**
 * The current user's notifications, polled every 20s for the bell. Mutations
 * (mark read / mark all read) invalidate the list so the unread badge updates.
 */
export function useNotifications(): UseNotifications {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: qk.notifications,
    queryFn: () => api.getNotifications(),
    refetchInterval: 20_000,
  });
  const items = data ?? [];

  const invalidate = () => qc.invalidateQueries({ queryKey: qk.notifications });
  const markAllRead = useMutation({
    mutationFn: () => api.markAllNotificationsRead(),
    onSuccess: invalidate,
  });
  const markRead = useMutation({
    mutationFn: (id: string) => api.markNotificationRead(id),
    onSuccess: invalidate,
  });

  return {
    items,
    unread: items.filter((n) => !n.read).length,
    markAllRead: () => markAllRead.mutate(),
    markRead: (id) => markRead.mutate(id),
  };
}

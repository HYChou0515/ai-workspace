import { useQuery } from "@tanstack/react-query";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { User } from "../api/types";

/**
 * The company directory, cached app-wide. Small (a few hundred), near-static →
 * `staleTime: Infinity`; fetched once and shared by every consumer (UserChip,
 * mention/share pickers).
 */
export function useUsers(): User[] {
  const { data } = useQuery({
    queryKey: qk.users,
    queryFn: () => api.getUsers(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return data ?? [];
}

/** Resolve a user id to its directory entry (or a placeholder while loading /
 * for an unknown id), so the UI never shows a bare id. */
export function useUser(userId: string): User {
  const users = useUsers();
  return (
    users.find((u) => u.id === userId) ?? {
      id: userId,
      name: userId,
      section: "",
      email: "",
      photo_url: null,
    }
  );
}

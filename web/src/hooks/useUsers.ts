import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "../api";
import { qk } from "../api/queryKeys";
import type { User } from "../api/types";

/**
 * The company directory, cached app-wide. Small (a few hundred), near-static →
 * `staleTime: Infinity`; fetched once and shared by every consumer (UserChip,
 * mention/share pickers).
 *
 * Deduped by id (#42): a directory may list a person once per section/group, so
 * the same id can appear several times. The pickers key rows by id, and a
 * repeated key breaks React's reconciliation when the list filters (stale rows
 * linger, matches append at the bottom, the person shows 2-4×). First wins.
 */
export function useUsers(): User[] {
  const { data } = useQuery({
    queryKey: qk.users,
    queryFn: () => api.getUsers(),
    staleTime: Number.POSITIVE_INFINITY,
  });
  return useMemo(() => {
    const seen = new Set<string>();
    return (data ?? []).filter((u) => {
      if (seen.has(u.id)) return false;
      seen.add(u.id);
      return true;
    });
  }, [data]);
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

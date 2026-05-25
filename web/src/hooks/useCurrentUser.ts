import { useEffect, useState } from "react";

import { api } from "../api";

/**
 * The signed-in user's id, fetched once via `api.getCurrentUser()`.
 *
 * Falls back to "default-user" while the fetch is in flight so owner/avatar
 * rendering and the "owned by me" filter never flash empty. When real auth
 * lands only `api.getCurrentUser` changes — callers stay the same.
 */
export function useCurrentUser(): string {
  const [user, setUser] = useState("default-user");
  useEffect(() => {
    let alive = true;
    api
      .getCurrentUser()
      .then((u) => alive && setUser(u))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return user;
}

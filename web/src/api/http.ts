/**
 * Same-origin fetch that respects a sub-path deploy. Vite bakes the deploy
 * base into `import.meta.env.BASE_URL` (e.g. "/my-svc/rca/" or "/"); we prefix
 * every API call with it so requests land under the same path the SPA is
 * served from. Use `apiFetch` instead of `fetch` for all backend calls.
 */

// "/my-svc/rca/" → "/my-svc/rca"; "/" → "" (so apiFetch("/x") === fetch("/x")).
export const API_BASE = import.meta.env.BASE_URL.replace(/\/+$/, "");

export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(API_BASE + path, init);
}

// How a ref in onboarding markdown becomes a browser URL (#161 images).
//
// An App ships the pictures its welcome teaching embeds under `<app>/assets/`,
// served by `GET /apps/{slug}/assets/{name}`; the markdown says
// `![](assets/<name>)` — the path on disk is the path in the text. That one
// shape is rewritten. An absolute path (`/onboarding/x.png`, the platform-level
// welcome's pictures under `web/public/`) or an external URL passes through as
// written, and so does any other relative ref: a ref nobody can serve is left
// to break visibly rather than guessed at.

import { API_PREFIX } from "../api/http";

export type OnboardingScope = { kind: "platform" } | { kind: "app"; slug: string };

const ASSETS = /^(?:\.\/)?assets\/([^/]+)$/;

export function onboardingAssetUrl(scope: OnboardingScope, src: string): string {
  if (scope.kind !== "app") return src;
  const m = ASSETS.exec(src);
  if (!m) return src;
  // On `API_PREFIX`, like every backend URL (#177): a sub-path deploy puts
  // its base in front, and a literal `/api/…` would point outside it.
  return `${API_PREFIX}/apps/${encodeURIComponent(scope.slug)}/assets/${encodeURIComponent(m[1])}`;
}

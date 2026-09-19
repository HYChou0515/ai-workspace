import { describe, expect, it, vi } from "vitest";

import { onboardingAssetUrl } from "./onboardingAssets";

/**
 * The assets URL on a SUB-PATH deploy (#177). Vite bakes the deploy base into
 * `import.meta.env.BASE_URL` and every backend URL is built on `API_PREFIX`
 * (`web/src/api/http.ts`) so it lands inside the ingress path — the App icon
 * does exactly this (`AppIcon.tsx`). A literal `/api/…` points outside the
 * deploy and the picture is a broken image in production. Its own file,
 * because the mock is module-wide and `onboardingAssets.test.ts` asserts the
 * base-less form.
 */
vi.mock("../api/http", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  API_PREFIX: "/my-svc/rca/api",
}));

describe("onboardingAssetUrl under a sub-path deploy", () => {
  it("puts the deploy base in front of the assets route, like every other backend URL", () => {
    expect(onboardingAssetUrl({ kind: "app", slug: "rca" }, "assets/hero.png")).toBe(
      "/my-svc/rca/api/apps/rca/assets/hero.png",
    );
  });
});

import { describe, expect, it } from "vitest";

import { onboardingAssetUrl } from "./onboardingAssets";

// How a ref in onboarding markdown becomes a browser URL. An App's images live
// under `<app>/assets/` and are served by `GET /apps/{slug}/assets/{name}`;
// everything that is not that one shape passes through untouched, so a wrong
// ref breaks visibly instead of being guessed at.
describe("onboardingAssetUrl", () => {
  const app = { kind: "app", slug: "rca" } as const;
  const platform = { kind: "platform" } as const;

  it("routes an App's assets/ ref to the assets endpoint", () => {
    expect(onboardingAssetUrl(app, "assets/hero.png")).toBe("/api/apps/rca/assets/hero.png");
    expect(onboardingAssetUrl(app, "./assets/hero.png")).toBe("/api/apps/rca/assets/hero.png");
  });

  it("encodes the slug and the file name", () => {
    expect(onboardingAssetUrl({ kind: "app", slug: "my app" }, "assets/a b.png")).toBe(
      "/api/apps/my%20app/assets/a%20b.png",
    );
  });

  it("passes absolute paths and external URLs through untouched", () => {
    expect(onboardingAssetUrl(app, "/onboarding/x.png")).toBe("/onboarding/x.png");
    expect(onboardingAssetUrl(app, "https://cdn.example/x.png")).toBe("https://cdn.example/x.png");
  });

  it("leaves any other relative ref alone — visibly broken, not guessed", () => {
    expect(onboardingAssetUrl(app, "hero.png")).toBe("hero.png");
    expect(onboardingAssetUrl(app, "images/hero.png")).toBe("images/hero.png");
    expect(onboardingAssetUrl(app, "assets/sub/hero.png")).toBe("assets/sub/hero.png");
  });

  it("never rewrites on the platform scope — it has no App to serve from", () => {
    expect(onboardingAssetUrl(platform, "assets/hero.png")).toBe("assets/hero.png");
    expect(onboardingAssetUrl(platform, "/onboarding/x.png")).toBe("/onboarding/x.png");
  });
});

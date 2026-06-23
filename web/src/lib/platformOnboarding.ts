// Platform-level welcome teaching (#161). Per-App teaching lives in each App's
// manifest; the platform welcome is about the shell itself (apps + KB), so it's
// a FE constant. Bump `version` (and the copy) to re-show it for everyone.

import type { Onboarding } from "../api/types";

export const PLATFORM_SCOPE = "platform";

export const PLATFORM_ONBOARDING: Onboarding = {
  version: "1",
  title: "Welcome to your workspace",
  intro: "A home for your apps and the knowledge they share.",
  points: [
    { title: "Open an app", body: "Each app is a focused workspace — pick one below to get started." },
    { title: "Knowledge Base", body: "Shared documents, wikis, and chat all live in the Knowledge Base." },
    { title: "Reopen this anytime", body: "Click the ? in the top bar to see this welcome again." },
  ],
};

// Drives the versioned welcome teaching (#161): auto-opens the modal until the
// user permanently dismisses the current version, and exposes reopen() for the
// persistent "?" help button (which works even after a permanent dismiss).

import { useCallback, useEffect, useState } from "react";

import type { Onboarding } from "../api/types";
import { dismiss, isDismissed } from "../lib/onboarding";

export type OnboardingState = {
  /** Whether the modal should currently render. */
  open: boolean;
  content?: Onboarding;
  /** "Got it" — close for now, but show again next mount (not persisted). */
  gotIt: () => void;
  /** "Don't show again" — permanently dismiss this version for this user+scope. */
  dontShowAgain: () => void;
  /** Manually reopen (the "?" help entry), regardless of dismissal. */
  reopen: () => void;
};

export function useOnboarding(
  userId: string,
  scope: string,
  content: Onboarding | undefined,
): OnboardingState {
  const [open, setOpen] = useState(false);
  const version = content?.version;

  useEffect(() => {
    if (version && !isDismissed(userId, scope, version)) setOpen(true);
  }, [userId, scope, version]);

  const gotIt = useCallback(() => setOpen(false), []);

  const dontShowAgain = useCallback(() => {
    if (version) dismiss(userId, scope, version);
    setOpen(false);
  }, [userId, scope, version]);

  const reopen = useCallback(() => {
    if (version) setOpen(true);
  }, [version]);

  return { open: open && !!content, content, gotIt, dontShowAgain, reopen };
}

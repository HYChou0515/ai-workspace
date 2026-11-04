// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from "vitest";

import { LATER_MS, reminderOf, remindLater, skipVersion } from "./skillUpdateReminders";

// plan-skill-hub-release-notes D3 / D4: the first reminder offers Update and
// Later; once a Later has run out, every reminder adds Skip. Later is 7 days.
// Kept per person, entry and version, in this browser.
const T0 = Date.UTC(2026, 10, 4, 9);
const DAY = 24 * 3600 * 1000;

describe("skill update reminders", () => {
  beforeEach(() => localStorage.clear());

  it("a version never answered is due, and cannot be skipped yet", () => {
    expect(reminderOf("alice", "e1", 3, T0)).toEqual({ due: true, canSkip: false, skipped: false, until: null });
  });

  it("Later hides it for exactly 7 days; after that it is due again, now with Skip", () => {
    expect(LATER_MS).toBe(7 * DAY);
    remindLater("alice", "e1", 3, T0);
    expect(reminderOf("alice", "e1", 3, T0 + 7 * DAY - 1)).toMatchObject({ due: false, until: T0 + 7 * DAY });
    expect(reminderOf("alice", "e1", 3, T0 + 7 * DAY)).toEqual({ due: true, canSkip: true, skipped: false, until: null });
  });

  it("Skip silences that version for good; a newer version is due again from the start", () => {
    remindLater("alice", "e1", 3, T0);
    skipVersion("alice", "e1", 3);
    expect(reminderOf("alice", "e1", 3, T0 + 100 * DAY)).toMatchObject({ due: false, skipped: true });
    expect(reminderOf("alice", "e1", 4, T0 + 100 * DAY)).toEqual({ due: true, canSkip: false, skipped: false, until: null });
  });

  it("is one person's answer: someone else in the same browser is still reminded", () => {
    remindLater("alice", "e1", 3, T0);
    expect(reminderOf("bob", "e1", 3, T0 + DAY).due).toBe(true);
    expect(reminderOf("alice", "e2", 3, T0 + DAY).due).toBe(true);
  });

  it("an unreadable store reminds rather than throws", () => {
    localStorage.setItem("skillHub.updateReminders", "{not json");
    expect(reminderOf("alice", "e1", 3, T0).due).toBe(true);
  });
});

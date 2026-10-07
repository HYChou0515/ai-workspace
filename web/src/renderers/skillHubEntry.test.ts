import { describe, expect, it } from "vitest";

import { parseShownSkillHubEntry } from "./skillHubEntry";

describe("parseShownSkillHubEntry", () => {
  it("reads the entry the tool declared after its sentence", () => {
    expect(
      parseShownSkillHubEntry('alice/triage is now shown in the chat.\n[skill-hub-entry]{"entry_id":"e-1"}'),
    ).toBe("e-1");
  });

  it.each([
    ["no output", undefined],
    ["prose only", "error: no skill hub entry 'x'"],
    ["the marker in prose", "write [skill-hub-entry] somewhere"],
    ["truncated while streaming", '…\n[skill-hub-entry]{"entry_id":"e-'],
    ["not an object", "…\n[skill-hub-entry][1]"],
    ["no id", '…\n[skill-hub-entry]{"entry_id":""}'],
    ["an id that is not a string", '…\n[skill-hub-entry]{"entry_id":7}'],
  ])("declares nothing for %s, and never throws", (_label, output) => {
    expect(parseShownSkillHubEntry(output)).toBeNull();
  });
});

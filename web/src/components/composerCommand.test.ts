/**
 * #739 P5: the composer understands a small set of slash commands.
 *
 * A command is not a message: it never reaches the model and is never persisted
 * as something the user said. Getting that wrong would put the literal text
 * "/compact" into the transcript the summariser is about to read.
 */
import { describe, expect, it } from "vitest";

import { parseComposerCommand } from "./composerCommand";

describe("parseComposerCommand", () => {
  it("recognises /compact", () => {
    expect(parseComposerCommand("/compact")).toBe("compact");
  });

  it("tolerates the whitespace a real person types", () => {
    expect(parseComposerCommand("  /compact  ")).toBe("compact");
  });

  it("is not case-sensitive", () => {
    expect(parseComposerCommand("/Compact")).toBe("compact");
  });

  it("leaves ordinary text alone", () => {
    expect(parseComposerCommand("compact this for me")).toBeNull();
    expect(parseComposerCommand("")).toBeNull();
  });

  it("does not swallow a message that merely starts with a slash", () => {
    // A path is a normal thing to type into a chat about a workspace. Treating
    // it as an unknown command and refusing to send it would be maddening.
    expect(parseComposerCommand("/etc/hosts 這個檔案在哪")).toBeNull();
    expect(parseComposerCommand("/compact 的實作在哪")).toBeNull();
  });

  it("does not invent commands it cannot run", () => {
    // An unknown slash word is just text. Silently eating it — or erroring —
    // would make the composer feel like it has a secret vocabulary.
    expect(parseComposerCommand("/clear")).toBeNull();
  });
});

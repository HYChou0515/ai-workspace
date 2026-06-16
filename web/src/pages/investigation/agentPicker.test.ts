import { describe, expect, it } from "vitest";

import { nameForPreset, pickerModels, presetForName } from "./agentPicker";

const picker = [
  { preset: "qwen3-local", name: "RCA · Qwen3 (local)" },
  { preset: "claude-opus", name: "RCA · Claude Opus" },
];

describe("agentPicker", () => {
  it("maps App picker entries to ModelEffortPicker models (name=display, model=preset)", () => {
    expect(pickerModels(picker)).toEqual([
      { name: "RCA · Qwen3 (local)", model: "qwen3-local" },
      { name: "RCA · Claude Opus", model: "claude-opus" },
    ]);
  });

  it("resolves the display name for the attached preset (selected entry)", () => {
    expect(nameForPreset(picker, "claude-opus")).toBe("RCA · Claude Opus");
    expect(nameForPreset(picker, "")).toBeNull();
  });

  it("resolves the preset to persist from a picked display name", () => {
    expect(presetForName(picker, "RCA · Qwen3 (local)")).toBe("qwen3-local");
    expect(presetForName(picker, "nope")).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import { parseViewSpec, setSkipWeekendsInYaml } from "./shared";

describe("setSkipWeekendsInYaml", () => {
  it("flips the value when the key already exists, keeping surrounding comments", () => {
    const text = ["view: gantt", "entity: issue", "# turn weekends off:", "skip_weekends: true", ""].join("\n");
    const off = setSkipWeekendsInYaml(text, false);
    expect(off).toContain("skip_weekends: false");
    expect(off).toContain("# turn weekends off:"); // comment preserved
    expect(parseViewSpec(off)?.skip_weekends).toBe(false);
  });

  it("appends the key when it's absent", () => {
    const text = "view: gantt\nentity: issue\n";
    const on = setSkipWeekendsInYaml(text, true);
    expect(parseViewSpec(on)?.skip_weekends).toBe(true);
    expect(on).toContain("view: gantt"); // original lines untouched
  });

  it("does not touch a commented-out skip_weekends line", () => {
    const text = "view: gantt\nentity: issue\n# skip_weekends: true (example)\n";
    const on = setSkipWeekendsInYaml(text, true);
    expect(on).toContain("# skip_weekends: true (example)"); // the comment stays
    expect(parseViewSpec(on)?.skip_weekends).toBe(true); // and a real key is added
  });
});

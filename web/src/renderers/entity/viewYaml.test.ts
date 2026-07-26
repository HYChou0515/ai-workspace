import { describe, expect, it } from "vitest";

import { parseViewSpec, setSkipWeekendsInYaml, setViewScalar } from "./shared";

describe("setViewScalar", () => {
  it("sets a string key comment-safe, adds it when absent, and removes it on null", () => {
    const base = "view: gantt\nentity: issue\n# who is grouped:\ngroup_by: milestone\n";
    const changed = setViewScalar(base, "group_by", "status");
    expect(changed).toContain("group_by: status");
    expect(changed).toContain("# who is grouped:"); // comment kept
    expect(parseViewSpec(changed)?.group_by).toBe("status");
    // absent → appended
    expect(parseViewSpec(setViewScalar("view: gantt\nentity: issue\n", "assignee_display", "name"))?.assignee_display).toBe(
      "name",
    );
    // null → the whole line is dropped
    const dropped = setViewScalar(base, "group_by", null);
    expect(dropped).not.toContain("group_by:");
    expect(dropped).toContain("# who is grouped:"); // its comment still there
  });
});

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

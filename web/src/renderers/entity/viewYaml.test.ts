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

describe("setViewScalar over a BLOCK value (#785)", () => {
  const text = [
    "view: gantt",
    "entity: issue",
    "# the working day:",
    "work_hours:",
    '  from: "07:00"',
    '  to: "21:00"',
    "label: title",
    "",
  ].join("\n");

  it("replaces the whole block, not just the line the key is on", () => {
    // Rewriting only `work_hours:` leaves its two children orphaned under a
    // flow mapping. That is not a wrong setting — it is a YAML parse error, and
    // `parseViewSpec` answers those with null, so the entire view goes blank
    // because someone moved a time picker.
    const changed = setViewScalar(text, "work_hours", '{ from: "08:00", to: "18:00" }');
    expect(parseViewSpec(changed)?.work_hours).toEqual({ from: 8, to: 18 });
    expect(parseViewSpec(changed)?.label).toBe("title");
    expect(changed).toContain("# the working day:");
  });

  it("takes a block SEQUENCE with it, dashes at the key's own indent", () => {
    // The ordinary way to write a YAML list: `- item` sits at the parent's
    // indent, not further in. An indent-only rule stops at the first dash, so
    // the items are orphaned under a flow mapping and the file stops parsing —
    // and `sort` is a key the panel writes, so this is reachable today.
    const text = [
      "view: gantt",
      "entity: issue",
      "sort:",
      "- field: title",
      "  dir: asc",
      "label: title",
      "",
    ].join("\n");
    const changed = setViewScalar(text, "sort", '[{"field":"status","dir":"desc"}]');
    expect(parseViewSpec(changed)?.sort).toEqual([{ field: "status", dir: "desc" }]);
    expect(parseViewSpec(changed)?.label).toBe("title");
  });

  it("keeps going past a blank line inside a block", () => {
    // A blank line between two settings in one block is formatting, not the end
    // of the block. Treating it as the end leaves the rest behind.
    const text = [
      "view: gantt",
      "entity: issue",
      "work_hours:",
      '  from: "07:00"',
      "",
      '  to: "21:00"',
      "label: title",
      "",
    ].join("\n");
    const changed = setViewScalar(text, "work_hours", '{ from: "08:00", to: "18:00" }');
    expect(parseViewSpec(changed)?.work_hours).toEqual({ from: 8, to: 18 });
    expect(parseViewSpec(changed)?.label).toBe("title");
  });

  it("keeps going past a comment at column 0 inside a block", () => {
    // The shipped view files are written almost entirely in column-0 comments,
    // so this is the likely shape, not an exotic one. An indent-only rule ends
    // the block at the comment and orphans everything after it — the same
    // "parse error → null → the whole view goes blank" this walk exists to
    // prevent. A comment is undecided for the same reason a blank line is: what
    // comes after it decides whether it was inside the block or before the next
    // key.
    const text = [
      "view: gantt",
      "entity: issue",
      "work_hours:",
      '  from: "07:00"',
      "# raised in March",
      '  to: "21:00"',
      "label: title",
      "",
    ].join("\n");
    const changed = setViewScalar(text, "work_hours", '{ from: "08:00", to: "18:00" }');
    expect(parseViewSpec(changed)?.work_hours).toEqual({ from: 8, to: 18 });
    expect(parseViewSpec(changed)?.label).toBe("title");
  });

  it("keeps a comment that introduces the NEXT key out of the block", () => {
    // The other half of the same rule, and the reason a comment cannot simply
    // be swallowed: the shipped files put a banner comment above each setting.
    const text = [
      "view: gantt",
      "entity: issue",
      "work_hours:",
      '  from: "07:00"',
      "",
      "# ── Time axis ─────",
      "weekday: number",
      "",
    ].join("\n");
    const changed = setViewScalar(text, "work_hours", null);
    expect(changed).toContain("# ── Time axis ─────");
    expect(parseViewSpec(changed)?.weekday).toBe("number");
    expect(parseViewSpec(changed)?.work_hours).toBeUndefined();
  });

  it("edits the TOP-LEVEL key, not a same-named one nested under another", () => {
    // `weekday` also exists inside a `card:` block here. Rewriting that one
    // leaves the real setting untouched, so the control appears to do nothing.
    const text = [
      "view: gantt",
      "entity: issue",
      "card:",
      "  weekday: long",
      "weekday: number",
      "",
    ].join("\n");
    const changed = setViewScalar(text, "weekday", "short");
    expect(parseViewSpec(changed)?.weekday).toBe("short");
    expect(changed).toContain("  weekday: long");
  });

  it("stops at the next key, not at the end of the file", () => {
    // The positive control for the two above: a greedier walk that ran to EOF
    // would satisfy them by eating everything.
    const text = ["view: gantt", "entity: issue", "group_by: status", "label: title", ""].join("\n");
    const changed = setViewScalar(text, "group_by", "milestone");
    expect(parseViewSpec(changed)?.group_by).toBe("milestone");
    expect(parseViewSpec(changed)?.label).toBe("title");
    expect(parseViewSpec(changed)?.entity).toBe("issue");
  });

  it("takes the block's children with it when the key is removed", () => {
    const dropped = setViewScalar(text, "work_hours", null);
    expect(parseViewSpec(dropped)?.work_hours).toBeUndefined();
    expect(parseViewSpec(dropped)?.label).toBe("title");
    expect(dropped).not.toContain("07:00");
  });
});

describe("work_hours (#785)", () => {
  const spec = (body: string) =>
    parseViewSpec(["view: gantt", "entity: issue", body, ""].join("\n"))?.work_hours;

  it("reads a working-hours window off the view file", () => {
    expect(spec('work_hours: {from: "07:00", to: "21:00"}')).toEqual({ from: 7, to: 21 });
  });

  it("reads a window that starts or ends on the half hour", () => {
    expect(spec('work_hours: {from: "08:30", to: "17:30"}')).toEqual({ from: 8.5, to: 17.5 });
  });

  it("ignores a window it cannot use rather than folding the whole day away", () => {
    // A half-written or inverted window would otherwise leave every bar zero
    // columns wide — a silently blank chart is a worse answer than no folding.
    expect(spec('work_hours: {from: "21:00", to: "07:00"}')).toBeUndefined();
    expect(spec('work_hours: {from: "07:00"}')).toBeUndefined();
    expect(spec('work_hours: {from: "07:00", to: "07:00"}')).toBeUndefined();
    expect(spec('work_hours: "all day"')).toBeUndefined();
    expect(spec('work_hours: {from: "25:00", to: "26:00"}')).toBeUndefined();
  });

  it("is absent when the view says nothing, so the day stays whole", () => {
    expect(spec("title: Timeline")).toBeUndefined();
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

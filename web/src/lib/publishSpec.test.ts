import { describe, expect, it } from "vitest";

import { choicesFromPublished, choicesFromSpec, republishChoices } from "./publishSpec";

describe("choicesFromSpec", () => {
  it("reads every key the skill writes, memory in GB", () => {
    expect(
      choicesFromSpec({
        sharing: "shared",
        reach: "subjects",
        subjects: ["user:amy", "group:ops"],
        role: "reader",
        cpu_cores: 2,
        memory_gb: 1.5,
      }),
    ).toEqual({
      sharing: "shared",
      reach: "subjects",
      subjects: ["user:amy", "group:ops"],
      role: "reader",
      cpu_cores: 2,
      memory_bytes: Math.round(1.5 * 1024 ** 3),
    });
  });

  it("pre-fills nothing that is malformed", () => {
    expect(
      choicesFromSpec({
        sharing: "everybody",
        reach: 3,
        subjects: ["amy", "user:", 7, "group:ops"],
        role: "owner",
        cpu_cores: 0,
        memory_gb: "4",
      }),
    ).toEqual({ subjects: ["group:ops"] });
  });

  it("is empty for no publish: at all, or one that is not a mapping", () => {
    for (const v of [undefined, null, "fresh", ["fresh"], 3]) expect(choicesFromSpec(v)).toEqual({});
  });
});

describe("choicesFromPublished", () => {
  it("is what is in effect now — every choice a re-publish must keep", () => {
    expect(
      choicesFromPublished({
        sharing: "shared",
        reach: "subjects",
        subjects: ["user:amy"],
        role: "reader",
        cpu_cores: 2,
        memory_bytes: 4 * 1024 ** 3,
      }),
    ).toEqual({
      sharing: "shared",
      reach: "subjects",
      subjects: ["user:amy"],
      role: "reader",
      cpu_cores: 2,
      memory_bytes: 4 * 1024 ** 3,
    });
  });
});

// docs/plan-wui-copy-update.md D6: the skill writes the release note into
// `publish:`; a re-publish's dialog opens with it — unless it was published.
describe("republishChoices", () => {
  const row = {
    sharing: "per_person" as const,
    reach: "everyone" as const,
    subjects: [],
    role: "participant" as const,
    cpu_cores: null,
    memory_bytes: null,
  };

  it("is what is in effect now, with the note the skill wrote", () => {
    expect(republishChoices({ ...row, latest_note: "" }, { note: "  Totals per week.\n" })).toEqual({
      ...choicesFromPublished(row),
      note: "Totals per week.",
    });
  });

  it("does not offer again a note already published", () => {
    expect(republishChoices({ ...row, latest_note: "Totals per week." }, { note: "Totals per week." }).note).toBe("");
  });

  it("does not offer again the draft it was last published from — however it was edited or cleared", () => {
    // Review round 1: the person edited the draft (or cleared it) in the
    // dialog; the file still says the old draft, and it came back next time.
    expect(
      republishChoices({ ...row, latest_note: "Totals, and a chart.", note_draft: "Totals." }, { note: "Totals." })
        .note,
    ).toBe("");
    expect(republishChoices({ ...row, latest_note: "Older.", note_draft: "Totals." }, { note: "Totals." }).note).toBe(
      "",
    );
    // A NEW draft is still offered.
    expect(republishChoices({ ...row, latest_note: "Older.", note_draft: "Totals." }, { note: "Legend." }).note).toBe(
      "Legend.",
    );
  });

  it("keeps who may use it from what is in effect, whatever the yaml says", () => {
    expect(republishChoices({ ...row, latest_note: "" }, { reach: "me", note: "x" }).reach).toBe("everyone");
  });
});

import { describe, expect, it } from "vitest";

import { choicesFromPublished, choicesFromSpec } from "./publishSpec";

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

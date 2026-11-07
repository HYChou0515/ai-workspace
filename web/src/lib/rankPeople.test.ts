/**
 * `rankPeople` — the people picker's order (docs/plan-people-picker-ranking.md
 * D5). Each case pits two people who differ in ONE factor against each other,
 * with every higher factor equal, so a case reddens only when its own rule
 * breaks.
 */
import { describe, expect, it } from "vitest";

import { rankPeople, type Person } from "./rankPeople";

const p = (id: string, name: string, section = "", email = ""): Person => ({ id, name, section, email });

const ids = (people: Person[]) => people.map((x) => x.id);

describe("rankPeople — before anything is typed", () => {
  it("lists everyone", () => {
    expect(ids(rankPeople([p("b", "B"), p("a", "A")], { query: "", viewerSection: "" }))).toHaveLength(2);
  });

  it("puts the people related to this place first", () => {
    const out = rankPeople([p("x", "Amy"), p("y", "Zed")], { query: "", viewerSection: "", related: ["y"] });
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("then the people picked often", () => {
    const out = rankPeople([p("x", "Amy"), p("y", "Zed")], {
      query: "",
      viewerSection: "",
      frecency: new Map([["y", 100]]),
    });
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("puts related above frequent", () => {
    const out = rankPeople([p("x", "Amy"), p("y", "Zed")], {
      query: "",
      viewerSection: "",
      related: ["y"],
      frecency: new Map([["x", 500]]),
    });
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("puts frequent above the viewer's own section", () => {
    const out = rankPeople([p("x", "Amy", "RD-PI-TF"), p("y", "Zed", "QA")], {
      query: "",
      viewerSection: "RD-PI-TF",
      frecency: new Map([["y", 10]]),
    });
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("then the people whose section shares the most leading characters with the viewer's", () => {
    const out = rankPeople([p("far", "Amy", "RD-QA"), p("near", "Zed", "RD-PI-CVD"), p("none", "Bob", "Sales")], {
      query: "",
      viewerSection: "RD-PI-TF",
    });
    expect(ids(out)).toEqual(["near", "far", "none"]);
  });

  it("gives no one a section advantage when the viewer has no section", () => {
    const out = rankPeople([p("x", "Zed", "RD"), p("y", "Amy", "")], { query: "", viewerSection: "" });
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("orders the rest by name, the way zh-TW sorts names", () => {
    const names = ["王小明", "陳大文", "Alice", "林美", "bob"];
    const out = rankPeople(
      names.map((n, i) => p(String(i), n)),
      { query: "", viewerSection: "" },
    );
    const collator = new Intl.Collator("zh-Hant-TW");
    expect(out.map((x) => x.name)).toEqual([...names].sort(collator.compare));
  });
});

describe("rankPeople — after typing", () => {
  const ctx = (query: string) => ({ query, viewerSection: "" });

  it("keeps only the people who match, in any field, ignoring case", () => {
    const people = [p("u1", "Amy Wang"), p("u2", "Bob", "", "WANG.b@x.com"), p("u3", "Cid", "Wangfu"), p("u4", "Dee")];
    expect(ids(rankPeople(people, ctx("wang"))).sort()).toEqual(["u1", "u2", "u3"]);
  });

  it("puts a name that starts with the text above one that contains it", () => {
    const out = rankPeople([p("a", "Lee Wang"), p("b", "Wang Lee")], ctx("wang"));
    expect(ids(out)).toEqual(["b", "a"]);
  });

  it("puts a name containing the text above an id or email that starts with it", () => {
    const out = rankPeople([p("wang01", "Amy"), p("x", "Lee Wang")], ctx("wang"));
    expect(ids(out)).toEqual(["x", "wang01"]);
  });

  it("puts an id or email that starts with the text above any other field that contains it", () => {
    const out = rankPeople([p("x", "Amy", "Wang team"), p("y", "Bob", "", "wang@x.com")], ctx("wang"));
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("ranks match quality above being related", () => {
    const out = rankPeople([p("x", "Lee Wang"), p("y", "Wang Lee")], { ...ctx("wang"), related: ["x"] });
    expect(ids(out)).toEqual(["y", "x"]);
  });

  it("within the same match quality: related, then frequent, then section, then name", () => {
    const people = [p("byName", "Wang A"), p("bySection", "Wang Z", "RD-PI"), p("byFreq", "Wang Y"), p("rel", "Wang X")];
    const out = rankPeople(people, {
      query: "wang",
      viewerSection: "RD-PI",
      related: ["rel"],
      frecency: new Map([["byFreq", 30]]),
    });
    expect(ids(out)).toEqual(["rel", "byFreq", "bySection", "byName"]);
  });

  it("treats a query of only spaces as nothing typed", () => {
    expect(rankPeople([p("a", "A"), p("b", "B")], ctx("   "))).toHaveLength(2);
  });
});

import { describe, expect, it } from "vitest";

import type { KbContextCard } from "../../api/kb";
import { lookupByName, scanPassage } from "./cardSearch";

const card = (norm_keys: string[], body: string): KbContextCard => ({
  id: body,
  collection_id: "c",
  keys: norm_keys,
  norm_keys,
  title: "",
  body,
});

describe("cardSearch", () => {
  it("looks up by exact name, normalized, not by substring", () => {
    const cards = [card(["m4"], "A"), card(["m40"], "B")];
    expect(lookupByName("M4", cards).map((c) => c.body)).toEqual(["A"]); // exact, M4≠M40
    expect(lookupByName("Ｍ４", cards).map((c) => c.body)).toEqual(["A"]); // full-width → m4
    expect(lookupByName("m40", cards).map((c) => c.body)).toEqual(["B"]);
    expect(lookupByName("nope", cards)).toEqual([]);
  });

  it("shows all cards when the name query is empty", () => {
    const cards = [card(["m4"], "A")];
    expect(lookupByName("  ", cards)).toEqual(cards);
  });

  it("scans a passage for mentioned cards with word boundaries", () => {
    const cards = [card(["m4"], "A"), card(["封蓋製程"], "B")];
    expect(scanPassage("what is M4 anyway", cards).map((c) => c.body)).toEqual(["A"]);
    expect(scanPassage("the m40 wafer", cards)).toEqual([]); // not a substring match
    expect(scanPassage("這個封蓋製程的問題", cards).map((c) => c.body)).toEqual(["B"]); // CJK embedded
  });

  it("shows all cards when the passage is empty", () => {
    const cards = [card(["m4"], "A")];
    expect(scanPassage("", cards)).toEqual(cards);
  });
});

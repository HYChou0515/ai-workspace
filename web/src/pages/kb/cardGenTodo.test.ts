import { describe, expect, it } from "vitest";

import type { KbProposedCard } from "../../api/kb";
import { parseTodo, serializeTodo } from "./cardGenTodo";

const p = (over: Partial<KbProposedCard>): KbProposedCard => ({
  keys: ["K"],
  title: "T",
  body: "B",
  confident: true,
  mode: "new",
  target_card_id: null,
  provenance: [],
  decision: "pending",
  ...over,
});

describe("cardGenTodo (#175)", () => {
  it("round-trips bodies unchanged", () => {
    const ps = [
      p({ keys: ["M4"], title: "Metal 4", body: "the cap" }),
      p({ keys: ["RZ3"], title: "Zone 3", body: "third zone" }),
    ];
    expect(parseTodo(serializeTodo(ps), ps).map((x) => x.body)).toEqual(["the cap", "third zone"]);
  });

  it("applies an edited body back to the right proposal", () => {
    const ps = [
      p({ keys: ["A"], title: "A", body: "old A" }),
      p({ keys: ["B"], title: "B", body: "old B" }),
    ];
    const out = parseTodo(serializeTodo(ps).replace("old B", "new B"), ps);
    expect(out[0].body).toBe("old A");
    expect(out[1].body).toBe("new B");
  });

  it("preserves non-body fields (keys / decision / mode / target)", () => {
    const ps = [
      p({
        keys: ["A", "Alpha"],
        decision: "accepted",
        mode: "update",
        target_card_id: "card-1",
      }),
    ];
    const out = parseTodo(serializeTodo(ps), ps);
    expect(out[0].keys).toEqual(["A", "Alpha"]);
    expect(out[0].decision).toBe("accepted");
    expect(out[0].mode).toBe("update");
    expect(out[0].target_card_id).toBe("card-1");
  });

  it("marks an uncertain card and strips the prefix on parse", () => {
    const ps = [p({ confident: false, body: "maybe" })];
    const md = serializeTodo(ps);
    expect(md).toContain("⚠️ uncertain — maybe");
    expect(parseTodo(md, ps)[0].body).toBe("maybe");
  });
});

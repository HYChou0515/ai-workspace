import { beforeEach, describe, expect, it } from "vitest";

import { mockApi } from "./mock";

describe("searchFiles / replaceInFiles (mock client)", () => {
  let inv: string;

  beforeEach(async () => {
    const created = await mockApi.createAppItem("rca", { title: "search test" });
    inv = created.resource_id;
    await mockApi.writeFile("rca", inv, "/a.md", "void rate spiked\nall good\nVOID again");
    await mockApi.writeFile("rca", inv, "/b.txt", "nothing here");
    await mockApi.writeFile("rca", inv, "/data/x.csv", "void in csv");
  });

  it("finds substring matches across files, case-insensitive by default", async () => {
    const res = await mockApi.searchFiles("rca", inv, "void");
    const byPath = Object.fromEntries(res.map((r) => [r.path, r.matches]));
    expect(byPath["/a.md"]?.map((m) => m.line)).toEqual([1, 3]);
    expect(byPath["/data/x.csv"]?.length).toBe(1);
    expect(byPath["/b.txt"]).toBeUndefined();
  });

  it("respects case-sensitive toggle", async () => {
    const res = await mockApi.searchFiles("rca", inv, "void", { caseSensitive: true });
    const a = res.find((r) => r.path === "/a.md")!;
    expect(a.matches.map((m) => m.line)).toEqual([1]);
  });

  it("respects whole-word toggle", async () => {
    await mockApi.writeFile("rca", inv, "/c.md", "void\navoidance\nvoid!");
    const res = await mockApi.searchFiles("rca", inv, "void", { wholeWord: true });
    const c = res.find((r) => r.path === "/c.md")!;
    expect(c.matches.map((m) => m.line)).toEqual([1, 3]);
  });

  it("supports regex", async () => {
    await mockApi.writeFile("rca", inv, "/log.txt", "err 500\nok 200\nerr 503");
    const res = await mockApi.searchFiles("rca", inv, "err \\d+", { regex: true });
    const log = res.find((r) => r.path === "/log.txt")!;
    expect(log.matches.map((m) => m.line)).toEqual([1, 3]);
  });

  it("honours include / exclude globs", async () => {
    const inc = await mockApi.searchFiles("rca", inv, "void", { include: "*.md" });
    expect(inc.map((r) => r.path)).toEqual(["/a.md"]);
    const exc = await mockApi.searchFiles("rca", inv, "void", { exclude: "data/**" });
    expect(exc.map((r) => r.path).sort()).toEqual(["/a.md"]);
  });

  it("empty query returns nothing", async () => {
    expect(await mockApi.searchFiles("rca", inv, "")).toEqual([]);
  });

  it("replaceInFiles rewrites matches and reports the count", async () => {
    const n = await mockApi.replaceInFiles("rca", inv, "void", "VOID");
    expect(n).toBe(3); // 2 in a.md + 1 in data/x.csv (case-insensitive)
    const a = await mockApi.readFile("rca", inv, "/a.md");
    expect(a.kind === "text" && a.text).toBe("VOID rate spiked\nall good\nVOID again");
  });
});

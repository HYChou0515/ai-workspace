import { beforeEach, describe, expect, it } from "vitest";

import { mockApi } from "./mock";

describe("directories (mock client)", () => {
  let inv: string;

  beforeEach(async () => {
    inv = (await mockApi.createAppItem("rca", { title: "dir test" })).resource_id;
  });

  it("mkdir creates an empty folder with no files", async () => {
    await mockApi.mkdir("rca", inv, "/notes");
    expect(await mockApi.listDirs("rca", inv)).toContain("/notes");
    expect(await mockApi.listFiles("rca", inv)).toEqual([]);
  });

  it("writing a nested file exposes its ancestor dirs", async () => {
    await mockApi.writeFile("rca", inv, "/data/raw/x.csv", "1");
    const dirs = await mockApi.listDirs("rca", inv);
    expect(dirs).toEqual(expect.arrayContaining(["/data", "/data/raw"]));
  });

  it("deleting the last file keeps the (now empty) folder", async () => {
    await mockApi.writeFile("rca", inv, "/d/a.txt", "a");
    await mockApi.deleteFile("rca", inv, "/d/a.txt");
    expect(await mockApi.listDirs("rca", inv)).toContain("/d");
  });

  it("deleting a folder removes its whole subtree", async () => {
    await mockApi.writeFile("rca", inv, "/d/a.txt", "a");
    await mockApi.writeFile("rca", inv, "/d/sub/b.txt", "b");
    await mockApi.deleteFile("rca", inv, "/d");
    expect(await mockApi.listDirs("rca", inv)).not.toContain("/d");
    expect(await mockApi.listFiles("rca", inv)).toEqual([]);
  });

  it("moving a folder relocates its subtree", async () => {
    await mockApi.writeFile("rca", inv, "/src/a.txt", "a");
    await mockApi.writeFile("rca", inv, "/src/sub/b.txt", "b");
    await mockApi.moveFile("rca", inv, "/src", "/dst");
    const files = (await mockApi.listFiles("rca", inv)).map((f) => f.path).sort();
    expect(files).toEqual(["/dst/a.txt", "/dst/sub/b.txt"]);
    expect(await mockApi.listDirs("rca", inv)).toContain("/dst");
    expect(await mockApi.listDirs("rca", inv)).not.toContain("/src");
  });
});

import { beforeEach, describe, expect, it } from "vitest";

import { mockApi } from "./mock";

describe("directories (mock client)", () => {
  let inv: string;

  beforeEach(async () => {
    inv = (await mockApi.createInvestigation({ title: "dir test" })).resource_id;
  });

  it("mkdir creates an empty folder with no files", async () => {
    await mockApi.mkdir(inv, "/notes");
    expect(await mockApi.listDirs(inv)).toContain("/notes");
    expect(await mockApi.listFiles(inv)).toEqual([]);
  });

  it("writing a nested file exposes its ancestor dirs", async () => {
    await mockApi.writeFile(inv, "/data/raw/x.csv", "1");
    const dirs = await mockApi.listDirs(inv);
    expect(dirs).toEqual(expect.arrayContaining(["/data", "/data/raw"]));
  });

  it("deleting the last file keeps the (now empty) folder", async () => {
    await mockApi.writeFile(inv, "/d/a.txt", "a");
    await mockApi.deleteFile(inv, "/d/a.txt");
    expect(await mockApi.listDirs(inv)).toContain("/d");
  });

  it("deleting a folder removes its whole subtree", async () => {
    await mockApi.writeFile(inv, "/d/a.txt", "a");
    await mockApi.writeFile(inv, "/d/sub/b.txt", "b");
    await mockApi.deleteFile(inv, "/d");
    expect(await mockApi.listDirs(inv)).not.toContain("/d");
    expect(await mockApi.listFiles(inv)).toEqual([]);
  });

  it("moving a folder relocates its subtree", async () => {
    await mockApi.writeFile(inv, "/src/a.txt", "a");
    await mockApi.writeFile(inv, "/src/sub/b.txt", "b");
    await mockApi.moveFile(inv, "/src", "/dst");
    const files = (await mockApi.listFiles(inv)).map((f) => f.path).sort();
    expect(files).toEqual(["/dst/a.txt", "/dst/sub/b.txt"]);
    expect(await mockApi.listDirs(inv)).toContain("/dst");
    expect(await mockApi.listDirs(inv)).not.toContain("/src");
  });
});

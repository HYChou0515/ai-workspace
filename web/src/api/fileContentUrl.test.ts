/**
 * The URL an `<img>`/`<a>` in the chat fetches a workspace file by.
 *
 * Both cases here came out of looking at a real turn in a real browser: paths
 * now arrive workspace-ABSOLUTE (the `shown_files` declaration normalises them),
 * and the old join produced `…/files//out/sine.png`. The backend tolerated the
 * double slash; a path-normalising proxy in front of it need not.
 */
import { describe, expect, it } from "vitest";

import { realApi } from "./real";

const url = (path: string) => realApi.fileContentUrl("playground", "item-1", path);

describe("fileContentUrl", () => {
  it("joins an absolute workspace path without doubling the slash", () => {
    expect(url("/out/sine.png")).toBe("/api/a/playground/items/item-1/files/out/sine.png");
  });

  it("takes a relative path just the same", () => {
    expect(url("out/sine.png")).toBe("/api/a/playground/items/item-1/files/out/sine.png");
  });

  it("encodes each segment but keeps the separators", () => {
    expect(url("/a b/ç#d.png")).toBe("/api/a/playground/items/item-1/files/a%20b/%C3%A7%23d.png");
  });
});

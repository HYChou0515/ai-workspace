// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";

import type { FileCaps, FileService } from "../../api/fileService";
import type { FileContent, FileInfo } from "../../api/types";
import { dispatchWuiRequest, type BridgeContext } from "./bridge";
import { WUI_PROTOCOL, type WuiRequest } from "./protocol";

const text = (path: string, body: string): FileContent => ({
  kind: "text",
  path,
  size: body.length,
  text: body,
  encoding: "utf-8",
});

function ctx(over: Partial<BridgeContext> = {}, files: Record<string, string> = {}): BridgeContext {
  const fs = {
    scopeId: "item1",
    caps: { write: true, delete: true } as FileCaps,
    listFiles: vi.fn(
      async (): Promise<FileInfo[]> =>
        Object.entries(files).map(([path, body]) => ({ path, size: body.length, read_only: false })),
    ),
    readFile: vi.fn(async (path: string) => {
      if (!(path in files)) throw new Error(`not found: ${path}`);
      return text(path, files[path]);
    }),
    writeFile: vi.fn(async () => {}),
    deleteFile: vi.fn(async () => {}),
    fileDownloadUrl: (path: string) => `/api/files${path}`,
  } as unknown as FileService;
  return {
    fs,
    folder: "/sales",
    openFile: null,
    me: "alice",
    declaredTools: [],
    callTool: null,
    declaredWorkflows: ["judge"],
    startRun: null,
    onRunEvent: () => {},
    ...over,
  };
}

const req = (verb: string, args: Record<string, unknown> = {}): WuiRequest => ({
  proto: WUI_PROTOCOL,
  id: "1",
  verb,
  args,
});

describe("dispatchWuiRequest", () => {
  it("reads any file in the item", async () => {
    const res = await dispatchWuiRequest(req("readFile", { path: "/notes.md" }), ctx({}, { "/notes.md": "hello" }));

    expect(res).toMatchObject({ ok: true, value: { kind: "text", text: "hello" } });
  });

  it("reads a bare path as one next to the page", async () => {
    const c = ctx({}, { "/sales/data.json": "[]" });
    const res = await dispatchWuiRequest(req("readFile", { path: "data.json" }), c);

    expect(res).toMatchObject({ ok: true });
    expect(c.fs.readFile).toHaveBeenCalledWith("/sales/data.json");
  });

  it("writes inside its own folder", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("writeFile", { path: "data.json", text: "[1]" }), c);

    expect(res).toMatchObject({ ok: true });
    expect(c.fs.writeFile).toHaveBeenCalledWith("/sales/data.json", "[1]");
  });

  it("refuses to write outside its folder, and says why in a sentence", async () => {
    // The refusal text ends up in front of someone who cannot open a console,
    // and gets forwarded to the agent verbatim — so `false` is not an answer.
    const c = ctx();
    const res = await dispatchWuiRequest(req("writeFile", { path: "/notes.md", text: "x" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/only write.*own folder/i);
    expect(res.ok === false && res.error).toContain("/notes.md");
    expect(c.fs.writeFile).not.toHaveBeenCalled();
  });

  it("tells a root-level page why it cannot save, and what to do about it", async () => {
    // The generic sentence would read "can only write inside its own folder" to
    // someone whose page has no folder — true, useless, and unfixable-sounding.
    const c = ctx({ folder: "" });
    const res = await dispatchWuiRequest(req("writeFile", { path: "data.json", text: "x" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/root/i);
    expect(res.ok === false && res.error).toMatch(/folder/i);
    expect(c.fs.writeFile).not.toHaveBeenCalled();
  });

  it("refuses to delete outside its folder", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("deleteFile", { path: "../notes.md" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(c.fs.deleteFile).not.toHaveBeenCalled();
  });

  it("deletes inside its folder, because writing already allows destroying", async () => {
    const c = ctx();
    const res = await dispatchWuiRequest(req("deleteFile", { path: "old.json" }), c);

    expect(res).toMatchObject({ ok: true });
    expect(c.fs.deleteFile).toHaveBeenCalledWith("/sales/old.json");
  });

  it("refuses a write when the service itself cannot write", async () => {
    const c = ctx({ fs: { ...ctx().fs, caps: { write: false, delete: false } as FileCaps } });
    const res = await dispatchWuiRequest(req("writeFile", { path: "a.json", text: "x" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/read-only/i);
  });

  it("lists the item's files", async () => {
    const res = await dispatchWuiRequest(req("listFiles"), ctx({}, { "/a.md": "x", "/sales/b.json": "y" }));

    expect(res).toMatchObject({ ok: true });
    expect(res.ok === true && (res.value as { files: FileInfo[] }).files).toHaveLength(2);
  });

  it("reads a listFiles prefix the same way every other verb reads a path", async () => {
    // It was the one verb whose relative spelling meant something different:
    // an author inside /sales writing `listFiles("data")` silently got the
    // workspace's /data.
    const c = ctx();
    await dispatchWuiRequest(req("listFiles", { prefix: "data" }), c);

    expect(c.fs.listFiles).toHaveBeenCalledWith("/sales/data");
  });

  it("still lists the whole item when asked for no prefix", async () => {
    const c = ctx();
    await dispatchWuiRequest(req("listFiles"), c);

    expect(c.fs.listFiles).toHaveBeenCalledWith("");
  });

  it("says who is looking", async () => {
    const res = await dispatchWuiRequest(req("whoami"), ctx());

    expect(res).toMatchObject({ ok: true, value: { user: "alice" } });
  });

  it("asks the workspace to open a file beside the page", async () => {
    const openFile = vi.fn();
    const res = await dispatchWuiRequest(req("openFile", { path: "/issues/5.md" }), ctx({ openFile }));

    expect(res).toMatchObject({ ok: true });
    expect(openFile).toHaveBeenCalledWith("/issues/5.md");
  });

  it("says so plainly when there is no workspace to open a file in", async () => {
    const res = await dispatchWuiRequest(req("openFile", { path: "/issues/5.md" }), ctx({ openFile: null }));

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toMatch(/cannot open/i);
  });

  it("runs a tool the page declared", async () => {
    const callTool = vi.fn(async () => ({ output: "{}", exit_code: 0 }));
    const c = ctx({ declaredTools: ["lot-status"], callTool });

    const res = await dispatchWuiRequest(
      req("callTool", { name: "lot-status", args: { lot: "A1" } }),
      c,
    );

    expect(res).toMatchObject({ ok: true, value: { exit_code: 0 } });
    expect(callTool).toHaveBeenCalledWith("lot-status", { lot: "A1" });
  });

  it("refuses a tool the page did not declare, and says where to declare it", async () => {
    // The declaration is disclosure — the server's ceiling is the real boundary
    // — but a page must not be able to use something it did not announce, and
    // the view file is the one place the reader can fix that.
    const callTool = vi.fn();
    const c = ctx({ declaredTools: [], callTool });

    const res = await dispatchWuiRequest(req("callTool", { name: "lot-status" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("tools:");
    expect(callTool).not.toHaveBeenCalled();
  });

  it("turns a failed tool call into a refusal a person can read", async () => {
    const c = ctx({
      declaredTools: ["lot-status"],
      callTool: vi.fn(async () => {
        throw new Error("This app does not offer lot-status to its pages.");
      }),
    });

    const res = await dispatchWuiRequest(req("callTool", { name: "lot-status" }), c);

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("does not offer");
  });

  it("names an unknown verb instead of failing silently", async () => {
    // The bridge's verb set is closed, so a page asking for something else is a
    // mistake worth reading — most likely an agent inventing an API.
    const res = await dispatchWuiRequest(req("callAgent"), ctx());

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("callAgent");
  });

  it("reports a missing file as a refusal, not a thrown render", async () => {
    const res = await dispatchWuiRequest(req("readFile", { path: "/gone.md" }), ctx());

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("/gone.md");
  });

  it("keeps a missing file in the page's OWN folder quiet — that is a first run", async () => {
    // The documented way to start empty, and the examples all catch it. Reported,
    // it would put a red refusal in front of every user opening every new WUI.
    const res = await dispatchWuiRequest(req("readFile", { path: "data.json" }), ctx());

    expect(res).toMatchObject({ ok: false, expected: true });
  });

  it("does NOT keep a missing file elsewhere in the item quiet", async () => {
    // A tool that returns a PATH rather than its payload — the way a tool with a
    // large result is supposed to answer — hands the page a string it did not
    // choose. If that path is wrong (a sandbox `/tmp/out.json`, say, which the
    // bridge resolves against the ITEM's root, where nothing exists), the page
    // gets the same "not there" a first run gets. Every example catches that and
    // renders its empty state, so the page says "nothing found", forever, and
    // the pane says nothing at all.
    //
    // The page's own folder is where absence is ordinary. Everywhere else it is
    // a mistake somebody has to be able to see.
    const res = await dispatchWuiRequest(req("readFile", { path: "/tmp/out.json" }), ctx());

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.expected).toBeUndefined();
  });

  it("hints at the root spelling without claiming to know the cause", async () => {
    // The reader cannot open a console, so the sentence has to name what is
    // actually surprising: a leading `/` means the ITEM's root, not a disk.
    //
    // But it must not ASSERT absence. `readAsset` files any error it cannot
    // classify as `missing` — `kbFileService` throws a plain Error for every
    // non-ok status, so a 403 lands here too — and this is the one sentence the
    // reader forwards to the agent. "Nothing could be read" is true either way;
    // "there is no file" would be a confident wrong diagnosis.
    const res = await dispatchWuiRequest(req("readFile", { path: "/tmp/out.json" }), ctx());
    const why = res.ok === false ? res.error : "";

    expect(why).toContain("/tmp/out.json");
    expect(why).toContain("this item's root");
    expect(why).not.toContain("There is no file");
  });

  it("refuses a bare path that starts with the page's own folder name", async () => {
    // The reported symptom, exactly: "/sales/sales/foo.json 無此檔案".
    //
    // A tool that writes into the page's folder names the file the way the
    // WORKSPACE names it — `sales/foo.json`, no leading slash, because that is
    // what a workspace path looks like everywhere else. A bare path here means
    // "next to the page", so the folder goes on twice.
    const res = await dispatchWuiRequest(req("readFile", { path: "sales/foo.json" }), ctx());

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.expected).toBeUndefined();
    const why = res.ok === false ? res.error : "";
    // Both spellings, because only the author knows which they meant.
    expect(why).toContain("/sales/sales/foo.json");
    expect(why).toContain("/sales/foo.json");
  });

  it("refuses the same spelling on the verbs that would SUCCEED", async () => {
    // The read is the least harmful member of the family and the only one that
    // was reported. `writeFile` succeeds, puts the file where nothing will look
    // for it, and returns ok — the save button works and the data is gone.
    // `listFiles` answers `[]`, which is a legitimate answer and therefore
    // indistinguishable from the truth. Hence ONE rule, before any verb runs.
    const c = ctx();
    for (const [verb, args] of [
      ["writeFile", { path: "sales/out.json", text: "[]" }],
      ["deleteFile", { path: "sales/out.json" }],
      ["openFile", { path: "sales/out.json" }],
      ["listFiles", { prefix: "sales/reports" }],
    ] as const) {
      const res = await dispatchWuiRequest(req(verb, args), c);
      expect(res, verb).toMatchObject({ ok: false });
      expect(res.ok === false && res.error, verb).toContain("in it twice");
    }
    // And nothing reached the workspace.
    expect(c.fs.writeFile).not.toHaveBeenCalled();
    expect(c.fs.deleteFile).not.toHaveBeenCalled();
    expect(c.fs.listFiles).not.toHaveBeenCalled();
  });

  it("lets the absolute spelling through, so nothing becomes impossible", async () => {
    // A page that really does keep files in `/sales/sales/` says so out loud.
    const c = ctx({}, { "/sales/sales/foo.json": "[1]" });
    const res = await dispatchWuiRequest(req("readFile", { path: "/sales/sales/foo.json" }), c);

    expect(res).toMatchObject({ ok: true });
  });

  it("starts a ROOT page quietly too, and does not lecture it about a slash", async () => {
    // A view file at the workspace root is a documented shape: it can read,
    // and every write is refused. Answering "is absence ordinary here?" with
    // `resolveWritePath` imported that write rule wholesale — it returns null
    // unconditionally for a root page — so EVERY missing read went loud,
    // including the bare read of its own data file on its very first open, with
    // a sentence about a leading "/" the caller never wrote.
    const res = await dispatchWuiRequest(req("readFile", { path: "data.json" }), ctx({ folder: "" }));

    expect(res).toMatchObject({ ok: false, expected: true });
    expect(res.ok === false && res.error).not.toContain("starting with");
  });

  it("still starts a page quietly when its own data file is simply not there", async () => {
    // The doubling rule must not swallow the ordinary first run — that is the
    // case `refuseExpected` exists for, and an alarm that fires on every new
    // page is one nobody reads.
    const res = await dispatchWuiRequest(req("readFile", { path: "data.json" }), ctx());

    expect(res).toMatchObject({ ok: false, expected: true });
  });

  it("does not cry doubling when the folder name legitimately repeats deeper", async () => {
    // `/sales/reports/sales/q1.json` really exists here, so nothing is wrong and
    // a successful read must not be second-guessed.
    const c = ctx({}, { "/sales/reports/sales/q1.json": "[1]" });
    const res = await dispatchWuiRequest(req("readFile", { path: "reports/sales/q1.json" }), c);

    expect(res).toMatchObject({ ok: true });
  });

  it("does not cry doubling for a deeper repeat that is merely absent", async () => {
    // The test above cannot pin this: it SUCCEEDS, so it never reaches the
    // missing branch at all, and a rule that matched any segment rather than the
    // first passed it untouched. Only an absent one exercises the decision.
    //
    // `/sales/reports/sales/q1.json` is an ordinary path inside the page's own
    // folder. Absent, it is an ordinary first run — quiet, and with no advice to
    // give, because there is no second spelling that would have worked.
    const res = await dispatchWuiRequest(req("readFile", { path: "reports/sales/q1.json" }), ctx());

    expect(res).toMatchObject({ ok: false, expected: true });
  });

  it("says nothing clever about a file named after its own folder", async () => {
    // `readFile("sales")` in `/sales` names `/sales/sales` — odd, legal, and with
    // no alternative spelling to offer. Without the guard the message suggests
    // "/sales/" and a second path built by slicing off a "/" that is not there.
    const res = await dispatchWuiRequest(req("readFile", { path: "sales" }), ctx());

    expect(res).toMatchObject({ ok: false, expected: true });
  });

  it("starts a run and reports every event back under the call's id", async () => {
    /**
     * The page gets the platform's events VERBATIM and decides what to draw.
     * The pane's chrome does not know which row somebody clicked, and the whole
     * premise of a WUI is that its author owns the experience.
     *
     * Each event carries the CALL's id, because a page may have two judgements
     * in flight and has no other way to tell whose progress it is looking at.
     */
    const events: unknown[] = [];
    const res = await dispatchWuiRequest(
      req("startRun", { workflow: "judge", with: { lot: "A1" } }),
      ctx({
        startRun: async (_workflow, _payload, onEvent) => {
          onEvent({ type: "step", text: "reading 12 files" });
          onEvent({ type: "done", exit_code: 0 });
          return { run_id: "run-1" };
        },
        onRunEvent: (id, event) => events.push([id, event]),
      }),
    );

    expect(res).toMatchObject({ ok: true, value: { run_id: "run-1" } });
    expect(events).toEqual([
      ["1", { type: "step", text: "reading 12 files" }],
      ["1", { type: "done", exit_code: 0 }],
    ]);
  });

  it("refuses a workflow the page did not declare", async () => {
    /**
     * Disclosure, exactly as with `tools:`. The app's list is the real gate on
     * the server; this stops a page QUIETLY using something it never announced,
     * which is what makes reading the declaration worth anything.
     */
    const res = await dispatchWuiRequest(
      req("startRun", { workflow: "undeclared" }),
      ctx({ declaredWorkflows: ["judge"], startRun: async () => ({ run_id: "x" }) }),
    );

    expect(res).toMatchObject({ ok: false });
    expect(res.ok === false && res.error).toContain("undeclared");
  });

  it("says so when this page is shown somewhere runs cannot start", async () => {
    const res = await dispatchWuiRequest(
      req("startRun", { workflow: "judge" }),
      ctx({ startRun: null }),
    );

    expect(res).toMatchObject({ ok: false });
  });

  it("answers on the id it was asked with, so replies cannot be crossed", async () => {
    const res = await dispatchWuiRequest({ ...req("whoami"), id: "abc" }, ctx());

    expect(res.id).toBe("abc");
    expect(res.proto).toBe(WUI_PROTOCOL);
  });
});

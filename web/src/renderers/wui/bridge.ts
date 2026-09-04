/**
 * The parent side of the WUI bridge — the gate.
 *
 * The frame cannot reach the API on its own (null origin), so everything it
 * does passes through here. Two rules shape the whole file:
 *
 * 1. **The ceiling is the viewer's own authority.** Every verb below is the
 *    thing the signed-in person could already do in this item through the UI,
 *    routed differently. So there is no second permission model to keep in step
 *    with the first — the backend refuses what it would have refused anyway.
 * 2. **Read broadly, write narrowly.** Reading the item is the point (secondary
 *    analysis of its real data); writing is confined to the page's own folder so
 *    a page cannot overwrite the item's notes or the folder next door.
 *
 * A refusal is a SENTENCE. It reaches someone who cannot open a console, and it
 * is what they forward to the agent, so "false" would waste the one message that
 * had a chance to explain.
 */

import type { FileService } from "../../api/fileService";
import type { OpenFile } from "../../hooks/openFile";
import { readAsset } from "./assets";
import { ok, refuse, refuseExpected, type WuiRequest, type WuiResponse } from "./protocol";
import { resolveReadPath, resolveWritePath } from "./paths";

export type CallTool = (
  name: string,
  args: Record<string, unknown>,
) => Promise<{ output: string; exit_code: number }>;

export type BridgeContext = {
  fs: FileService;
  /** The page's own folder — the write boundary. */
  folder: string;
  /** The workspace's file opener, or `null` outside a shell that has one. */
  openFile: OpenFile | null;
  /** The signed-in user's id, or `null` while it is still loading. */
  me: string | null;
  /**
   * The tools this page's view file declares (`tools:` in the yaml).
   *
   * Disclosure, not the security boundary — the app's `tools[]` ceiling is
   * enforced on the server and a page can never exceed it. What this adds is
   * that a page cannot QUIETLY use something it did not announce, which is what
   * makes the declaration worth reading before opening one.
   */
  declaredTools: string[];
  /** Run one package tool, or `null` where no backend is wired. */
  callTool: CallTool | null;
  /**
   * Called after this page changes a file, with the resolved path.
   *
   * Every write is broadcast back to everyone looking at the item, the writer
   * included — so without this a page hears "somebody else changed this" after
   * each of its own saves. The bridge reports the fact; what to do about the
   * echo is the pane's business, not a rule about writing.
   */
  onWrote?: (path: string) => void;
};

/**
 * Why this page may not change that path.
 *
 * A page whose view file sits at the workspace root has no folder of its own,
 * so the ordinary sentence ("only inside its own folder") is true, useless and
 * reads as unfixable. Naming the cause names the fix: move the file.
 */
function cannotWrite(folder: string, verb: string, target: string): string {
  return folder
    ? `This page can only write inside its own folder (${folder}), so it cannot ${verb} ${target}.`
    : `This page's view file is at the workspace root, so it has no folder of its own to write in and cannot ${verb} ${target}. Move the page into a folder.`;
}

const str = (args: Record<string, unknown> | undefined, key: string): string | null => {
  const v = args?.[key];
  return typeof v === "string" ? v : null;
};

export async function dispatchWuiRequest(
  req: WuiRequest,
  ctx: BridgeContext,
): Promise<WuiResponse> {
  const { id, verb, args } = req;
  const { fs, folder } = ctx;

  switch (verb) {
    case "listFiles": {
      const raw = str(args, "prefix") ?? "";
      // Read-scoped like every other path, so a relative spelling means the
      // same thing here as it does in `readFile`. The spellings for "the whole
      // item" — absent, empty, `/`, `.` — all stay empty: they name the root,
      // which has no path to resolve, and scoping them was a regression that
      // refused a listing that used to work.
      const prefix = raw && raw !== "/" && raw !== "." ? resolveReadPath(folder, raw) : "";
      if (prefix === null) return refuse(id, `${raw} is not a path in this workspace.`);
      return ok(id, { files: await fs.listFiles(prefix) });
    }

    case "readFile": {
      const raw = str(args, "path");
      if (raw === null) return refuse(id, "readFile needs a `path`.");
      const path = resolveReadPath(folder, raw);
      if (path === null) return refuse(id, `${raw} is not a path in this workspace.`);
      const read = await readAsset(fs, path);
      // Only ABSENCE is ordinary. A 403 or a 500 arriving as "there is no file"
      // was already misleading; once not-found stopped being reported at all it
      // became silent, which is how a read-only viewer's page does nothing and
      // says nothing.
      //
      // And absence is ordinary only in the page's OWN folder, where a first run
      // reads a data file nobody has written yet. Elsewhere it is a mistake, and
      // the shape it takes is specific: a tool with a large result answers with a
      // PATH rather than its payload, so the page reads a string it did not
      // choose. A sandbox path like `/tmp/out.json` resolves against THIS item's
      // root, where nothing is — and the page, catching absence the way every
      // example teaches, renders its empty state and says "nothing found",
      // forever, while the pane stays silent because the refusal was marked
      // expected. Both halves have to be wrong for that to happen, so this fixes
      // the half that can tell the difference.
      if (read.kind === "missing") {
        const mine = resolveWritePath(folder, raw) !== null;
        if (mine) return refuseExpected(id, `There is no file at ${path}.`);
        // Names the thing that is actually surprising. The reader cannot open a
        // console, and "there is no file" leaves them nothing to act on.
        return refuse(
          id,
          `There is no file at ${path} in this item. A path starting with "/" is read from this item's root, not from a sandbox or a disk.`,
        );
      }
      if (read.kind === "failed") return refuse(id, read.reason);
      return ok(id, { path, ...read.asset });
    }

    case "writeFile": {
      const raw = str(args, "path");
      const text = str(args, "text");
      if (raw === null || text === null) return refuse(id, "writeFile needs a `path` and `text`.");
      if (!fs.caps.write) return refuse(id, "This workspace is read-only, so nothing can be saved.");
      const path = resolveWritePath(folder, raw);
      if (path === null) return refuse(id, cannotWrite(folder, "save to", raw));
      await fs.writeFile(path, text);
      ctx.onWrote?.(path);
      return ok(id, { path });
    }

    case "deleteFile": {
      const raw = str(args, "path");
      if (raw === null) return refuse(id, "deleteFile needs a `path`.");
      if (!fs.caps.delete) return refuse(id, "This workspace is read-only, so nothing can be deleted.");
      const path = resolveWritePath(folder, raw);
      if (path === null) return refuse(id, cannotWrite(folder, "delete", raw));
      await fs.deleteFile(path);
      ctx.onWrote?.(path);
      return ok(id, { path });
    }

    case "openFile": {
      const raw = str(args, "path");
      if (raw === null) return refuse(id, "openFile needs a `path`.");
      // Reading is the right yardstick: this only asks the workspace to show a
      // file the person could have clicked on themselves.
      const path = resolveReadPath(folder, raw);
      if (path === null) return refuse(id, `${raw} is not a path in this workspace.`);
      if (!ctx.openFile) return refuse(id, "This page cannot open files from where it is shown.");
      ctx.openFile(path);
      return ok(id, { path });
    }

    case "callTool": {
      const name = str(args, "name");
      if (name === null) return refuse(id, "callTool needs a tool `name`.");
      if (!ctx.declaredTools.includes(name)) {
        // Named, and told what to do about it: the page's view file is the one
        // place this can be fixed, and the person reading has no other way to
        // find that out.
        return refuse(
          id,
          `This page did not declare ${name}. Add it to \`tools:\` in the page's view file.`,
        );
      }
      if (!ctx.callTool) return refuse(id, "Tools cannot be run from where this page is shown.");
      const toolArgs = args?.args;
      try {
        return ok(id, await ctx.callTool(name, (toolArgs ?? {}) as Record<string, unknown>));
      } catch (err) {
        return refuse(id, err instanceof Error ? err.message : `${name} could not be run.`);
      }
    }

    case "whoami":
      return ok(id, { user: ctx.me });

    default:
      // The verb set is closed on purpose, so an unknown one is most often an
      // agent inventing an API — worth naming rather than dropping.
      return refuse(id, `This page asked for "${verb}", which a WUI cannot do.`);
  }
}

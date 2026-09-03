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
import { ok, refuse, type WuiRequest, type WuiResponse } from "./protocol";
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
};

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
      const prefix = str(args, "prefix") ?? "";
      return ok(id, { files: await fs.listFiles(prefix) });
    }

    case "readFile": {
      const raw = str(args, "path");
      if (raw === null) return refuse(id, "readFile needs a `path`.");
      const path = resolveReadPath(folder, raw);
      if (path === null) return refuse(id, `${raw} is not a path in this workspace.`);
      const asset = await readAsset(fs, path);
      if (asset === null) return refuse(id, `There is no file at ${path}.`);
      return ok(id, { path, ...asset });
    }

    case "writeFile": {
      const raw = str(args, "path");
      const text = str(args, "text");
      if (raw === null || text === null) return refuse(id, "writeFile needs a `path` and `text`.");
      if (!fs.caps.write) return refuse(id, "This workspace is read-only, so nothing can be saved.");
      const path = resolveWritePath(folder, raw);
      if (path === null) {
        return refuse(
          id,
          `This page can only write inside its own folder${folder ? ` (${folder})` : ""}, so it cannot save to ${raw}.`,
        );
      }
      await fs.writeFile(path, text);
      return ok(id, { path });
    }

    case "deleteFile": {
      const raw = str(args, "path");
      if (raw === null) return refuse(id, "deleteFile needs a `path`.");
      if (!fs.caps.delete) return refuse(id, "This workspace is read-only, so nothing can be deleted.");
      const path = resolveWritePath(folder, raw);
      if (path === null) {
        return refuse(
          id,
          `This page can only delete inside its own folder${folder ? ` (${folder})` : ""}, so it cannot delete ${raw}.`,
        );
      }
      await fs.deleteFile(path);
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

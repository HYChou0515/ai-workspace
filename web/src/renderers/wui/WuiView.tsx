/**
 * The WUI pane: a workspace folder, running.
 *
 * Rendered by `AiYamlRenderer` ahead of the entity dispatcher — the same route
 * `health` takes, and for the same two reasons: this kind needs the view FILE's
 * path (its folder is the whole unit) and it wants the pane full-bleed rather
 * than inside the entity panel's chrome.
 *
 * The iframe's attributes are the security model, not decoration:
 * `sandbox="allow-scripts"` WITHOUT `allow-same-origin` gives the frame a null
 * origin — no cookies, no parent DOM, no API — so `postMessage` is its only way
 * out and the parent is the gate. See `assemble.ts` for the CSP that closes the
 * remaining one, the network.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useFileService } from "../../api/fileService";
import { qk } from "../../api/queryKeys";
import { viewParamString } from "../entity/shared";
import type { ViewSpec } from "../entity/types";
import { buildWuiDoc } from "./assets";
import { wuiFolder } from "./paths";

/** The conventional entry, overridable with `entry:` in the view file. */
export const DEFAULT_ENTRY = "index.html";

export function WuiView({ path, spec }: { path: string; spec: ViewSpec }) {
  const fs = useFileService();
  const folder = wuiFolder(path);
  const entry = viewParamString(spec, "entry") ?? DEFAULT_ENTRY;

  // Nothing reloads a WUI on its own (plan decision 9): an agent editing the
  // page while someone is halfway through using it should not yank the page out
  // from under them. Refresh bumps this, which is a new query key, which is a
  // fresh document — and a fresh frame, so the page's state goes with it.
  const [generation, setGeneration] = useState(0);

  const built = useQuery({
    queryKey: qk.wuiDoc(fs.scopeId, path, generation),
    queryFn: () => buildWuiDoc(fs, folder, entry),
    staleTime: Infinity,
    retry: false,
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "4px 8px",
          borderBottom: "1px solid var(--paper-3)",
          flex: "0 0 auto",
        }}
      >
        <button type="button" onClick={() => setGeneration((g) => g + 1)}>
          Refresh
        </button>
      </div>
      {built.isPending ? (
        <div style={{ padding: 12, color: "var(--text-paper-d)" }}>Opening…</div>
      ) : built.error ? (
        // Plain language and the file's name: whoever hits this may have no
        // console to open, and this text is what they forward to the agent.
        <div role="status" style={{ padding: 12, color: "var(--err)" }}>
          {built.error instanceof Error ? built.error.message : "This WUI could not be opened."}
        </div>
      ) : (
        <iframe
          title={viewParamString(spec, "title") ?? folder.split("/").pop() ?? "WUI"}
          sandbox="allow-scripts"
          srcDoc={built.data.doc}
          style={{ flex: 1, width: "100%", minHeight: 0, border: 0, background: "#fff" }}
        />
      )}
    </div>
  );
}

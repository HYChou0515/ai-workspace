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
import { useEffect, useRef, useState } from "react";

import { useFileService } from "../../api/fileService";
import { qk } from "../../api/queryKeys";
import { useCurrentUserState } from "../../hooks/useCurrentUser";
import { useOpenFile } from "../../hooks/openFile";
import { subscribeFileChanged } from "../../lib/fileChangedBus";
import { viewParamString } from "../entity/shared";
import type { ViewSpec } from "../entity/types";
import { buildWuiDoc } from "./assets";
import { dispatchWuiRequest } from "./bridge";
import { wuiFolder } from "./paths";
import { WUI_PROTOCOL, isWuiRequest, type WuiEvent } from "./protocol";

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

  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const openFile = useOpenFile();
  const { id: me, ready: meReady } = useCurrentUserState();

  // The gate. Everything the page can do arrives here, and the only thing that
  // makes a message OURS is that it came from this frame's window — origin is
  // useless (a sandboxed frame's is the string "null", which every sandboxed
  // frame shares).
  useEffect(() => {
    const onMessage = (ev: MessageEvent) => {
      const win = frameRef.current?.contentWindow;
      if (!win || ev.source !== win) return;
      if (!isWuiRequest(ev.data)) return;
      void dispatchWuiRequest(ev.data, {
        fs,
        folder,
        openFile,
        me: meReady ? me : null,
      }).then((res) => {
        // `"*"` because the recipient's origin IS "null" and cannot be named.
        // Safe here only because the target is this specific frame, reached
        // through the handle we hold rather than by broadcasting to the page.
        win.postMessage(res, "*");
      });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [fs, folder, openFile, me, meReady]);

  // Forwarded, not acted on: the platform cannot know whether a half-finished
  // form should be thrown away, and only the page does.
  useEffect(
    () =>
      subscribeFileChanged(fs.scopeId, (changed) => {
        const event: WuiEvent = { proto: WUI_PROTOCOL, event: "file_changed", path: changed };
        frameRef.current?.contentWindow?.postMessage(event, "*");
      }),
    [fs.scopeId],
  );

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
          ref={frameRef}
          title={viewParamString(spec, "title") ?? folder.split("/").pop() ?? "WUI"}
          sandbox="allow-scripts"
          srcDoc={built.data.doc}
          style={{ flex: 1, width: "100%", minHeight: 0, border: 0, background: "#fff" }}
        />
      )}
    </div>
  );
}

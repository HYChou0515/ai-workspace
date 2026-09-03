/**
 * The WUI pane: a workspace folder, running.
 *
 * Rendered by `AiYamlRenderer` ahead of the entity dispatcher — the same route
 * `health` takes, and for the same two reasons: this kind needs the view FILE's
 * path (its folder is the whole unit) and it wants the pane full-bleed rather
 * than inside the entity panel's chrome.
 *
 * The boundary is made of three things, in two files and not only this one:
 *
 * - `sandbox="allow-scripts"` WITHOUT `allow-same-origin` — a null origin, so
 *   no cookies, no parent DOM, no API, and `postMessage` is the only way out.
 * - The CSP in `assemble.ts` — no fetch, no XHR, no WebSocket, no remote
 *   subresource, so the page cannot send what it read.
 * - `SPA_CSP`'s `frame-src` in `api/spa.py` — the page cannot NAVIGATE itself
 *   somewhere else either. That one cannot live in the frame: a document's own
 *   CSP has no say over its own navigation, and without it `location.href` was
 *   an open exfiltration route past the other two.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { useFileService } from "../../api/fileService";
import { qk } from "../../api/queryKeys";
import { Btn } from "../../components/Btn";
import { useCurrentUserState } from "../../hooks/useCurrentUser";
import { useOpenFile } from "../../hooks/openFile";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { publishAgentDraft } from "../../lib/agentDraftBus";
import { subscribeFileChanged } from "../../lib/fileChangedBus";
import { pxToRem } from "../../lib/pxToRem";
import { viewParam, viewParamString } from "../entity/shared";
import { itemCallTool } from "./api";
import type { ViewSpec } from "../entity/types";
import { buildWuiDoc } from "./assets";
import { dispatchWuiRequest } from "./bridge";
import { wuiFolder } from "./paths";
import { createSelfWrites } from "./selfWrites";
import { WUI_PROTOCOL, isWuiRequest, refuse, type WuiEvent } from "./protocol";
import {
  formatReportsForAgent,
  isWuiReportMessage,
  reportHeadline,
  type WuiReport,
} from "./report";

/** The conventional entry, overridable with `entry:` in the view file. */
export const DEFAULT_ENTRY = "index.html";

/**
 * How many of a page's reports we keep.
 *
 * The runtime reports every uncaught error, and the archetypal agent bug is one
 * inside a timer — one message per frame, forever. An unbounded list re-rendered
 * per message locks up the whole app, not just the pane, and the button that
 * would clear it is inside the frozen tree. The newest are kept: the first error
 * is usually the cause, but the ones a person is looking at are the ones that
 * just happened.
 */
export const MAX_REPORTS = 100;

/** How many of the OLDEST reports survive a trim. The comment above says the
 * first error is usually the cause — keeping only the newest would discard
 * exactly that, so both ends are kept and the gap is stated. */
const KEEP_FIRST = 20;

/** The id the gap marker always carries, so a trim can find its own previous
 * marker and keep counting instead of restarting at one. */
const GAP_ID = -1;

/** Trim to `MAX_REPORTS`, keeping both ends and recording what fell out.
 * A truncated list that reads as a complete transcript is worse than a short
 * one: the agent is told what happened and not told that more did. The count is
 * CUMULATIVE — trimming one at a time is the normal case, and a marker that
 * says "1 more" after nine hundred were dropped is a wrong number, not a
 * rounding. */
export function trimReports(reports: WuiReport[]): WuiReport[] {
  if (reports.length <= MAX_REPORTS) return reports;
  const previous = reports.find((r) => r.id === GAP_ID)?.dropped ?? 0;
  const real = reports.filter((r) => r.id !== GAP_ID);
  const tail = real.slice(-(MAX_REPORTS - KEEP_FIRST - 1));
  // Count what is actually lost. The marker occupies one of the slots, so
  // `reports.length - MAX_REPORTS` under-counts by one on every trim — and
  // trimming happens once per message, so the error compounds into exactly the
  // wrong number this marker exists to prevent.
  const dropped = previous + (real.length - KEEP_FIRST - tail.length);
  return [
    ...reports.slice(0, KEEP_FIRST),
    {
      id: GAP_ID,
      kind: "error" as const,
      message: `… and ${dropped} more report${dropped === 1 ? "" : "s"} in between, dropped.`,
      detail: null,
      dropped,
    },
    ...tail,
  ];
}

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
  const [reports, setReports] = useState<WuiReport[]>([]);
  const nextReportId = useRef(0);
  const selfWrites = useRef(createSelfWrites());

  // What this page says it uses. Disclosure rather than the boundary — the
  // app's ceiling is enforced on the server — but a page that could quietly
  // call anything the app grants would make the declaration not worth reading.
  const declaredTools = useMemo(() => {
    const raw = viewParam(spec, "tools");
    return Array.isArray(raw) ? raw.filter((t): t is string => typeof t === "string") : [];
  }, [spec]);
  const slug = useWorkspaceSlug();
  const callTool = useMemo(
    () => (slug ? itemCallTool(slug, fs.scopeId) : null),
    [slug, fs.scopeId],
  );

  /** Post to the frame. `"*"` because an opaque origin cannot be named as a
   * target; what makes that safe is `SPA_CSP` (see the note on the reply path
   * below), not this handle. */
  const toFrame = (msg: unknown) => frameRef.current?.contentWindow?.postMessage(msg, "*");

  // The gate. Everything the page can do arrives here, and the only thing that
  // makes a message OURS is that it came from this frame's window — origin is
  // useless (a sandboxed frame's is the string "null", which every sandboxed
  // frame shares).
  useEffect(() => {
    const onMessage = (ev: MessageEvent) => {
      const win = frameRef.current?.contentWindow;
      if (!win || ev.source !== win) return;

      if (isWuiReportMessage(ev.data)) {
        const { report, message, detail } = ev.data;
        setReports((rs) =>
          trimReports([...rs, { id: nextReportId.current++, kind: report, message, detail }]),
        );
        return;
      }

      if (!isWuiRequest(ev.data)) return;
      const request = ev.data;
      void dispatchWuiRequest(request, {
        fs,
        folder,
        openFile,
        me: meReady ? me : null,
        declaredTools,
        callTool,
        onWrote: (written) => selfWrites.current.record(written),
      })
        // A file op can reject for reasons the gate cannot see — a 403 for a
        // read-only viewer, a 507 for a full workspace. Unanswered, the page's
        // `await` never settles: a save button that does nothing, forever, with
        // no message. That is the one outcome this bridge exists to prevent, so
        // every path out of here posts a sentence.
        .catch((err: unknown) =>
          refuse(request.id, err instanceof Error ? err.message : `${request.verb} failed.`),
        )
        .then((res) => {
          // `"*"` because an opaque origin cannot be named as a target. What
          // makes that safe is not this handle — a WindowProxy keeps its
          // identity across navigation — but `SPA_CSP`'s `frame-src` on the
          // containing document, which forbids this frame becoming anything
          // else. See `api/spa.py`.
          win.postMessage(res, "*");
        });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [fs, folder, openFile, me, meReady, declaredTools, callTool]);

  // Forwarded, not acted on: the platform cannot know whether a half-finished
  // form should be thrown away, and only the page does.
  useEffect(
    () =>
      subscribeFileChanged(fs.scopeId, (changed) => {
        // The broadcast goes to everyone looking at the item, the writer
        // included. Told about its own save, an editor warns "somebody else
        // changed this" every time it saves — and the one warning that matters
        // arrives already discredited.
        if (selfWrites.current.consume(changed)) return;
        const event: WuiEvent = { proto: WUI_PROTOCOL, event: "file_changed", path: changed };
        frameRef.current?.contentWindow?.postMessage(event, "*");
      }),
    [fs.scopeId],
  );

  const tellTheAgent = () => {
    publishAgentDraft(fs.scopeId, formatReportsForAgent(folder, reports));
    setReports([]);
  };

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
        <Btn size="sm" onClick={() => setGeneration((g) => g + 1)}>
          Refresh
        </Btn>
        <Btn size="sm" onClick={() => toFrame({ proto: WUI_PROTOCOL, command: "pick", on: true })}>
          Report a problem
        </Btn>
        {reports.length > 0 && (
          <Btn size="sm" variant="primary" onClick={tellTheAgent}>
            Tell the agent ({reports.length})
          </Btn>
        )}
      </div>
      {reports.length > 0 && (
        <div
          role="log"
          style={{
            flex: "0 0 auto",
            maxHeight: "30%",
            overflowY: "auto",
            padding: "6px 8px",
            borderBottom: "1px solid var(--paper-3)",
            fontSize: pxToRem(12),
          }}
        >
          {reports.map((r) => (
            <div key={r.id} style={{ color: r.kind === "pick" ? "var(--text-paper-d)" : "var(--err)" }}>
              {reportHeadline(r)}
            </div>
          ))}
        </div>
      )}
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

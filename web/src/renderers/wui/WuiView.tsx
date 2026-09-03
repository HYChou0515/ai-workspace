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
import { HttpError } from "../../api/http";
import { publishAgentDraft } from "../../lib/agentDraftBus";
import { subscribeFileChanged } from "../../lib/fileChangedBus";
import { pxToRem } from "../../lib/pxToRem";
import { autoBuildScope, useWuiAutoBuild } from "../../lib/wuiAutoBuild";
import { viewParam, viewParamString } from "../entity/shared";
import { itemCallTool } from "./api";
import { cleanBuildOutput, hasBuildScript, itemBuild } from "./build";
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

  /**
   * Does this page have a build step?
   *
   * `scripts.build` decides, because `pnpm run build` is what the route runs.
   * Most pages are plain files with nothing to build, and a Rebuild button in
   * front of them would fail loudly over a page that is perfectly fine.
   *
   * The workspace root is excluded: a root-level page has no folder to build in
   * and the route answers 400, so offering the button would only make the
   * platform look broken.
   */
  const buildable = useQuery({
    queryKey: qk.wuiBuildable(fs.scopeId, folder),
    queryFn: () =>
      fs
        .readFile(`${folder}/package.json`)
        .then((content) => (content.kind === "text" ? content.text : ""))
        // No manifest is the ordinary case, not a fault worth reporting.
        .catch(() => ""),
    staleTime: Infinity,
    retry: false,
  });
  const canBuild = folder !== "" && hasBuildScript(buildable.data ?? "");

  /** The build's output, newest last. `null` means no build has been run — the
   * panel is absent rather than empty, so the pane costs nothing until someone
   * asks for it. */
  const [buildLog, setBuildLog] = useState<string[] | null>(null);
  const [building, setBuilding] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);
  const [autoBuild, setAutoBuild] = useWuiAutoBuild(autoBuildScope(fs.scopeId, folder));
  /** The page this pane has already rebuilt on open, so that "when I open this"
   * means what it says: once. React re-runs the effect whenever the preference
   * changes — and in StrictMode, twice on mount — and neither is somebody
   * opening the page. */
  const autoBuiltFor = useRef<string | null>(null);

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

  // Keep the newest line in view. A build's interesting output is its last few
  // lines — where it failed, or how long it took — and a log that has to be
  // scrolled to be read is a log nobody reads.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [buildLog]);

  const say = (line: string) => setBuildLog((lines) => [...(lines ?? []), line]);

  /**
   * Rebuild the page, showing the build's output as it arrives.
   *
   * On success the page is re-read — the whole point is that `dist/` and what
   * you are looking at stop being different things. On failure it is left
   * alone: the build produced no new `dist/`, so swapping the frame would
   * replace the page with the same page and call it a rebuild.
   */
  const runBuild = async ({ automatic = false } = {}) => {
    if (!slug) return;
    setBuilding(true);
    setBuildLog([]);
    try {
      for await (const event of itemBuild(slug, fs.scopeId)(folder)) {
        if (event.type === "output") say(cleanBuildOutput(event.text));
        else if (event.exit_code === 0) {
          say("Build finished.");
          setGeneration((g) => g + 1);
        } else say(`Build failed (exit ${event.exit_code}).`);
      }
    } catch (err) {
      // A build that could not be STARTED — a viewer without `execute`, a
      // folder the server refuses — arrives as a status, not as output. Unsaid,
      // the button looks like it did nothing at all.
      say(err instanceof Error ? err.message : "The build could not be run.");
      // A 403 is permanent for this person: they may read the item and not run
      // things in it. Left on, rebuilding on open would greet them with that
      // same refusal every single time they open the page — so it turns itself
      // off, and says that it did. Only for the AUTOMATIC path, and only for a
      // refusal: a build that failed to start once because the network hiccuped
      // must not quietly disable itself forever.
      if (automatic && err instanceof HttpError && err.status === 403) {
        setAutoBuild(false);
        say("Rebuilding on open has been turned off, because you cannot run things here.");
      }
    } finally {
      setBuilding(false);
    }
  };

  // Rebuild on open, when this page is set to. This is the setting under which
  // going stale is IMPOSSIBLE rather than unlikely — and it is a setting, not a
  // rule, because the cost (tens of seconds, and waking the item's sandbox) is
  // real enough that someone may not want to pay it on every open.
  useEffect(() => {
    if (!canBuild || !slug) return; // not a built page, or not known yet
    if (autoBuiltFor.current === folder) return;
    // The opening moment is spent HERE, whether or not it builds. Marking it
    // only on the way to a build made ticking the box later count as opening
    // the page, which is not what the box says.
    autoBuiltFor.current = folder;
    if (!autoBuild) return;
    void runBuild({ automatic: true });
    // `runBuild` is deliberately not a dependency: it is rebuilt every render,
    // and the guard above is what decides when this may run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canBuild, autoBuild, slug, folder]);

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
        <Btn
          size="sm"
          onClick={() => {
            setBuildLog(null);
            setGeneration((g) => g + 1);
          }}
        >
          Refresh
        </Btn>
        {canBuild && (
          <>
            <Btn size="sm" disabled={building} onClick={() => void runBuild()}>
              {building ? "Building…" : "Rebuild"}
            </Btn>
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: pxToRem(12),
                color: "var(--text-paper-d)",
              }}
            >
              <input
                type="checkbox"
                checked={autoBuild}
                onChange={(e) => setAutoBuild(e.target.checked)}
              />
              Rebuild when I open this
            </label>
          </>
        )}
        <Btn size="sm" onClick={() => toFrame({ proto: WUI_PROTOCOL, command: "pick", on: true })}>
          Report a problem
        </Btn>
        {reports.length > 0 && (
          <Btn size="sm" variant="primary" onClick={tellTheAgent}>
            Tell the agent ({reports.length})
          </Btn>
        )}
      </div>
      {buildLog !== null && (
        // The build's own words, verbatim and monospaced, because they are a
        // compiler's and their columns mean something.
        <div
          ref={logRef}
          role="log"
          aria-label="Build output"
          style={{
            flex: "0 0 auto",
            maxHeight: "30%",
            overflowY: "auto",
            padding: "6px 8px",
            borderBottom: "1px solid var(--paper-3)",
            fontFamily: "var(--font-mono, ui-monospace, monospace)",
            fontSize: pxToRem(12),
            whiteSpace: "pre-wrap",
            color: "var(--text-paper-d)",
          }}
        >
          {buildLog.length === 0 ? "Starting the build…" : buildLog.join("")}
        </div>
      )}
      {reports.length > 0 && (
        <div
          role="log"
          aria-label="Reports"
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
      ) : built.error && building ? (
        // The first open of a page nobody has built yet: `dist/` really is
        // absent, and saying so in red — under a log showing the build that is
        // about to create it — is alarming and, seconds later, untrue.
        <div role="status" style={{ padding: 12, color: "var(--text-paper-d)" }}>
          Building… the page appears when this finishes.
        </div>
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

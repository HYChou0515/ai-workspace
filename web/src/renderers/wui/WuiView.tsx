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

import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { useFileService, type FileService } from "../../api/fileService";
import { qk } from "../../api/queryKeys";
import { Btn } from "../../components/Btn";
import { Switch } from "../../components/Switch";
import { useCurrentUserState } from "../../hooks/useCurrentUser";
import { useOpenFile } from "../../hooks/openFile";
import { useWorkspaceSlug } from "../../hooks/useWorkspaceSlug";
import { API_BASE, HttpError } from "../../api/http";
import { encodePath } from "../../api/real";
import { publishAgentDraft } from "../../lib/agentDraftBus";
import { subscribeFileChanged } from "../../lib/fileChangedBus";
import { pxToRem } from "../../lib/pxToRem";
import { autoBuildScope, useWuiAutoBuild } from "../../lib/wuiAutoBuild";
import { viewParam, viewParamString } from "../entity/shared";
import { itemCallTool } from "./api";
import { cleanBuildOutput, hasBuildScript, itemBuild } from "./build";
import { itemRun } from "./run";
import type { ViewSpec } from "../entity/types";
import { buildWuiDoc, WuiEntryMissing } from "./assets";
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
 * The pane's document, as ONE query definition. The pane observes it and
 * Deploy fetches it — the same options object, so what Deploy verified is,
 * by construction, what the pane then shows. Two hand-written copies of the
 * key and function are how the verdict and the frame came to read the
 * folder separately and disagree on screen; a copy that drifts (an `entry`
 * added to the key, a `staleTime` changed on one side) would bring that
 * back with every test green.
 */
function wuiDocQuery(fs: FileService, folder: string, entry: string, path: string, generation: number) {
  return queryOptions({
    queryKey: qk.wuiDoc(fs.scopeId, path, generation),
    queryFn: () => buildWuiDoc(fs, folder, entry),
    staleTime: Infinity,
    retry: false,
  });
}

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

/**
 * Whose page this is. `workspace` is the author's: the toolbar (Refresh,
 * Rebuild, Auto-rebuild, Report a problem, Tell the agent), the build log and
 * the reports. `viewer` is the reader's — somebody who followed the page's own
 * URL (`/w/…`, `WuiPage`): they have nothing to rebuild, nobody to tell and
 * nothing to pick, so none of that is drawn. An explicit prop rather than a
 * context, because `WuiPage` already records what a missing provider does here
 * — silently nothing — and that shape must not exist twice.
 */
export type WuiChrome = "workspace" | "viewer";

export function WuiView({
  path,
  spec,
  chrome = "workspace",
}: {
  path: string;
  spec: ViewSpec;
  chrome?: WuiChrome;
}) {
  const fs = useFileService();
  const queryClient = useQueryClient();
  const folder = wuiFolder(path);
  const entry = viewParamString(spec, "entry") ?? DEFAULT_ENTRY;
  /** The author's chrome — the toolbar, the build log, the reports, Deploy,
   * and the reads that only feed them. One name for every gate, so "a reader
   * sees and costs none of it" is one fact rather than comparisons that could
   * drift apart. */
  const author = chrome === "workspace";

  // Nothing reloads a WUI on its own (plan decision 9): an agent editing the
  // page while someone is halfway through using it should not yank the page out
  // from under them. Refresh bumps this, which is a new query key, which is a
  // fresh document — and a fresh frame, so the page's state goes with it.
  const [generation, setGeneration] = useState(0);

  const built = useQuery(wuiDocQuery(fs, folder, entry, path, generation));

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
    // Never for a root-level page: `canBuild` is false there whatever the
    // answer, so the read is a 404 nobody can use. And never for a reader:
    // the answer feeds Rebuild and Deploy, neither of which they are shown,
    // so the read was a round trip on every link open that nothing consumed.
    enabled: author && folder !== "",
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
  /** True while the build this pane started ON OPEN is still running.
   *
   * Opening a page and building it at once makes the page's own reads race the
   * sandbox restore the build triggers: for a moment `app.js` and `style.css`
   * come back missing, so nothing is inlined and the frame renders unstyled and
   * inert under three red lines. It comes right when the build finishes and the
   * folder is re-read — which is the reason not to show the first version at
   * all. (The race itself is older and wider than this pane: `files/facade.py`'s
   * `_warm` routes reads to a sandbox that merely EXISTS, without asking
   * `is_ready`, so any read during a restore can answer "not there".) */
  const [firstBuild, setFirstBuild] = useState(false);
  /** Whether the log is unfolded. It earns the top of the pane while the build
   * runs and for as long as something went wrong; a build that SUCCEEDED has
   * already said everything it has to say, and leaving twelve lines of vite
   * output above somebody's page is taking their pane for a receipt. */
  const [logOpen, setLogOpen] = useState(true);
  const logRef = useRef<HTMLDivElement | null>(null);
  const [autoBuild, setAutoBuild] = useWuiAutoBuild(autoBuildScope(fs.scopeId, folder));
  /** Deploy: re-read the manifest, rebuild where there is a build, confirm the
   * page OPENS, then hand over its address. One status rather than parallel
   * flags: `working` is Deploy's own run (the manifest read, its build, the
   * open check — `building` alone is shared with Rebuild and covers only the
   * middle step); `done` shows the address; `failed` names which step, so
   * "see the build output" is never said over a log that is not there.
   *
   * Keyed by the PATH it is about, and rendered only while that is the pane's
   * path. A verdict is a fact about one view file: two view files in one
   * folder otherwise shared a "✓ Deployed" over whichever address was
   * current, and a run that finished after the pane had moved to the sibling
   * landed its verdict there. Keying it here replaces a reset effect and a
   * render-time ref that tried to track the same thing. */
  const [deploy, setDeploy] = useState<
    { path: string } & (
      | { state: "idle" }
      | { state: "working" }
      | { state: "done"; generation: number }
      | { state: "failed"; step: "build" }
      | { state: "failed"; step: "open"; why: string }
      | { state: "failed"; step: "unknown" }
    )
  >({ path, state: "idle" });
  /** The HOLD is the pane's, whatever page it is on: a run for A is still a
   * build in this folder while the pane shows B, and B's Rebuild pressed then
   * is the second build the hold exists to prevent. */
  const deploying = deploy.state === "working";
  /** What is SHOWN is the page's — a verdict about a sibling is not shown —
   * and, for a success, the read's: "✓ Deployed" is a fact about the
   * generation it verified, so a Refresh (a new read) retires it rather
   * than leaving an address over whatever the new read found. */
  const deployHere =
    deploy.path !== path || (deploy.state === "done" && deploy.generation !== generation)
      ? ({ path, state: "idle" } as const)
      : deploy;
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");
  /** The page this pane has already rebuilt on open, so that "when I open this"
   * means what it says: once. React re-runs the effect whenever the preference
   * changes — and in StrictMode, twice on mount — and neither is somebody
   * opening the page. */
  const autoBuiltFor = useRef<string | null>(null);
  /** Bumped whenever the pane moves to another page. A build started for one
   * page can still be running when `path` changes without unmounting, and
   * everything it does on the way out — the log, the verdict, the re-read that
   * swaps the frame — would land on a page that never asked for it.
   *
   * A counter bumped in an EFFECT, not the folder written during render: React
   * may render without committing, and a ref set by a render that was thrown
   * away would tell a running build it had been left when it had not. */
  const epoch = useRef(0);
  /** The build in flight, so leaving can actually stop it. `stale()` only stops
   * this pane ACTING on a build; the server hears nothing until the request is
   * aborted. */
  const inFlight = useRef<AbortController | null>(null);
  /** The committed `generation`, readable from inside an async run that
   * outlives the render it was started in — Deploy names the generation it
   * verifies. Written in an EFFECT, for the reason `epoch` gives above: a
   * ref written during a render that is then thrown away would hand the run
   * a generation no commit ever had. */
  const generationRef = useRef(generation);
  useEffect(() => {
    generationRef.current = generation;
  }, [generation]);

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

  /** What the view file declared it may start. Disclosure, not the gate — the
   *  app's own list is enforced on the server. Same shape as `tools:`. */
  const declaredWorkflows = useMemo(() => {
    const raw = viewParam(spec, "workflows");
    return Array.isArray(raw) ? raw.filter((w): w is string => typeof w === "string") : [];
  }, [spec]);

  const startRun = useMemo(() => {
    if (!slug) return null;
    const run = itemRun(slug, fs.scopeId);
    return async (
      workflow: string,
      payload: Record<string, unknown>,
      onEvent: (event: unknown) => void,
    ) => {
      // The stream is pumped here and each event handed straight on, so the page
      // sees progress WHILE the run is happening rather than a single answer at
      // the end. That is the whole point of making this a run.
      // Draining the stream IS the wait: the promise settles when the run's
      // events stop. It returns nothing — there was a `{ run_id }` in the
      // contract that this could only ever answer with "", because the route
      // replies with a stream and the id it discards never reaches a page.
      for await (const event of run(workflow, payload)) onEvent(event);
    };
  }, [slug, fs.scopeId]);

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
        // A reader is shown no reports, so none are kept: a page erroring
        // inside a timer would otherwise re-render this pane — and its frame —
        // once per message for a list nobody can see.
        if (!author) return;
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
        declaredWorkflows,
        startRun,
        // Each event carries the CALL's id: a page may have two judgements in
        // flight and has no other way to tell whose progress it is looking at.
        onRunEvent: (id, event) =>
          win.postMessage({ proto: WUI_PROTOCOL, id, event: "run_event", payload: event }, "*"),
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
  }, [author, fs, folder, openFile, me, meReady, declaredTools, callTool, declaredWorkflows, startRun]);

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

  // Moving to another page starts clean. Its log, its fold, and the fact that
  // one has already been built on open are all facts about the page that left —
  // and until the log survived Refresh (P12), clearing it there hid this.
  useEffect(() => {
    epoch.current += 1;
    inFlight.current?.abort();
    inFlight.current = null;
    setBuildLog(null);
    setLogOpen(true);
    setBuilding(false);
    setFirstBuild(false);
    // NOT `autoBuiltFor`. It already holds the FOLDER it built, so the guard is
    // folder-aware without help — and clearing it here defeated that guard
    // between StrictMode's two effect passes, which is two builds on mount.
  }, [folder]);

  // Closing the pane is leaving too: without this the build outlives the whole
  // view, not just the page.
  //
  // The guard goes with it. StrictMode mounts, unmounts and mounts again, so
  // this cleanup runs between the two passes — aborting the build the first
  // pass started while `autoBuiltFor` still said one had been done, which left
  // the page never built at all. Clearing it makes the pair idempotent, and a
  // real remount is somebody opening the page again, which is when rebuilding
  // is exactly what was asked for.
  useEffect(
    () => () => {
      inFlight.current?.abort();
      autoBuiltFor.current = null;
      // Leaving the view is leaving the folder: a Deploy still in its
      // manifest re-read would otherwise wake with `moved()` false and START
      // a build for a page nobody is looking at, with no one left to abort
      // it. The same epoch every run already watches.
      epoch.current += 1;
    },
    [],
  );

  // Keep the newest line in view. A build's interesting output is its last few
  // lines — where it failed, or how long it took — and a log that has to be
  // scrolled to be read is a log nobody reads.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [buildLog]);

  /** A chunk of the build's own output, exactly as it arrived. */
  const say = (chunk: string) => setBuildLog((lines) => [...(lines ?? []), chunk]);

  /** One of OUR lines, which always gets a line to itself. A build whose last
   * chunk did not end in a newline — normal, since chunks are byte fragments —
   * would otherwise read "…built in 581msBuild finished." */
  const note = (line: string) =>
    setBuildLog((lines) => {
      const prev = lines ?? [];
      const glued = prev.length > 0 && !prev[prev.length - 1].endsWith("\n");
      return [...prev, `${glued ? "\n" : ""}${line}\n`];
    });

  /**
   * Rebuild the page, showing the build's output as it arrives, and say
   * whether it finished with exit 0 — `false` for a failure, a build that
   * could not start, a stream that ended without a verdict, or one the pane
   * moved away from. Deploy is the caller that needs the answer: it hands
   * over the page's address only once what the address points at is fresh.
   *
   * On success the page is re-read — the whole point is that `dist/` and what
   * you are looking at stop being different things. Deploy passes
   * `reload: false` and does that read itself, through the pane's query, so
   * the verdict and the frame come from ONE read. On failure it is left
   * alone: the build produced no new `dist/`, so swapping the frame would
   * replace the page with the same page and call it a rebuild.
   */
  const runBuild = async ({ automatic = false, reload = true } = {}): Promise<boolean> => {
    if (!slug) return false;
    // One value, one assignment per branch: `null` is "no verdict arrived".
    let outcome: null | "ok" | "failed" = null;
    const mine = folder;
    const startedAt = epoch.current;
    // Every path that could start a second build already stopped the first:
    // leaving the page aborts it, closing the pane aborts it, and the button is
    // disabled while one runs. A pre-emptive abort here guarded nothing that
    // could be reached, so it is not here.
    const control = new AbortController();
    inFlight.current = control;
    /** Has the pane moved on? Then this build is answering a question nobody is
     * asking any more, and the page it would re-read is not the page it built. */
    const stale = () => epoch.current !== startedAt;
    setBuilding(true);
    if (automatic) setFirstBuild(true);
    setBuildLog([]);
    setLogOpen(true);
    try {
      for await (const event of itemBuild(slug, fs.scopeId)(mine, control.signal)) {
        if (stale()) return false;
        if (event.type === "output") say(event.text);
        else if (event.exit_code === 0) {
          outcome = "ok";
          note("Build finished.");
          // Fold it away: the page below IS the result, and it is what the
          // reader came for. One line stays, and opens it again.
          setLogOpen(false);
          if (reload) setGeneration((g) => g + 1);
        } else {
          outcome = "failed";
          note(`Build failed (exit ${event.exit_code}).`);
        }
      }
      // A stream that closed without a `done` — a proxy's read timeout on a
      // long build, a connection cut mid-way — is not a failure the log
      // shows, and a caller told "see the build output" would find ordinary
      // vite lines and no verdict. Say so, in the log, where they will look.
      if (outcome === null && !stale()) note("The build's output ended without a verdict.");
    } catch (err) {
      // An abort is this pane's own doing, not something to report.
      if (stale() || control.signal.aborted) return false;
      // A build that could not be STARTED — a viewer without `execute`, a
      // folder the server refuses — arrives as a status, not as output. Unsaid,
      // the button looks like it did nothing at all.
      note(err instanceof Error ? err.message : "The build could not be run.");
      // A 403 is permanent for this person: they may read the item and not run
      // things in it. Left on, rebuilding on open would greet them with that
      // same refusal every single time they open the page — so it turns itself
      // off, and says that it did. Only for the AUTOMATIC path, and only for a
      // refusal: a build that failed to start once because the network hiccuped
      // must not quietly disable itself forever.
      if (automatic && err instanceof HttpError && err.status === 403) {
        setAutoBuild(false);
        note("Rebuilding on open has been turned off, because you cannot run things here.");
      }
    } finally {
      if (inFlight.current === control) inFlight.current = null;
      // Gated like every other write. The page you left keeps building while
      // the page you arrived at starts its own; clearing these unconditionally
      // declared the SECOND build over while it was still running — Rebuild
      // enabled again, and the page shown before the build that was going to
      // replace it had finished. Moving away already reset them, so there is
      // nothing to strand.
      if (!stale()) {
        setBuilding(false);
        setFirstBuild(false);
      }
    }
    return outcome === "ok";
  };

  // Rebuild on open, when this page is set to. This is what closes the gap for
  // everyone who OPENS the page — not a guarantee: the manifest read below is
  // allowed to fail quietly, and a build that fails leaves the previous `dist/`
  // up. It is a setting, not a rule, because the cost (tens of seconds, and
  // waking the item's sandbox) is real enough that someone may not want to pay
  // it on every open.
  useEffect(() => {
    // A reader is handed what is already built, never a build — before the
    // setting is even consulted, so the preference an author left on cannot
    // wake a sandbox on a reader's account.
    if (!author) return;
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
  }, [chrome, canBuild, autoBuild, slug, folder]);

  // Cleaned HERE, over the joined stream, not chunk by chunk: an escape
  // sequence is a byte fragment too, and half of one arriving in the previous
  // chunk left its tail on screen as literal text.
  const logText = buildLog === null ? "" : cleanBuildOutput(buildLog.join(""));
  const logLines = logText.trimEnd().split("\n");
  const logSummary = logLines[logLines.length - 1] || "Build output";

  const tellTheAgent = () => {
    publishAgentDraft(fs.scopeId, formatReportsForAgent(folder, reports));
    setReports([]);
  };

  // The page's own address — what WuiPage answers at `/w/:slug/:itemId/*`
  // (App.tsx). Under the DEPLOY BASE (`API_BASE`, "" or "/my-svc/rca"): the
  // router mounts there, and a link that started at the origin left the SPA
  // on every sub-path deploy. Slug and item id encoded the way `itemCallTool`
  // encodes them; the path segment by segment, so a folder with a space or a
  // CJK name still round-trips through the router's decoding.
  const address = `${window.location.origin}${API_BASE}/w/${encodeURIComponent(slug)}/${encodeURIComponent(fs.scopeId)}/${encodePath(path)}`;

  const copyAddress = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied("done");
    } catch {
      // No clipboard here (a non-secure context). The field above is the
      // fallback — say so rather than looking like the button did nothing.
      setCopied("failed");
    }
  };

  /** Rebuild where there is a build, confirm the page opens, then hand over
   * the address — the publisher builds, the reader never does, so what the
   * address points at is fresh at the moment it is handed over
   * (docs/plan-wui-deploy.md).
   *
   * Three facts are established HERE, not read off state:
   * - whether there is a build — the manifest is re-read on press. The
   *   cached answer (`canBuild`) is from the moment the pane opened, and a
   *   page the agent gave a build to since then deployed as "nothing to
   *   build". Pressing before that first read has landed simply joins it.
   * - whether the build passed — `runBuild`'s answer.
   * - whether the page OPENS — ONE read, through the pane's own query, so
   *   the frame shows exactly what was verified. Exit 0 is not that: a build
   *   whose outDir is not the entry, or a plain page with no `index.html`,
   *   was declared Deployed above a red error. And a private read outside
   *   the query was not it either: it passed while the pane kept its cached
   *   red error, and the two disagreed on screen.
   *
   * While it runs the pane is Deploy's: Rebuild, Auto-rebuild and Refresh are
   * held (the buttons), and this IS the rebuild-on-open for this folder
   * (`autoBuiltFor`), or the manifest re-read flipping `canBuild` would let
   * the on-open effect start a second build beside this one.
   *
   * The verdict is about the view file that was pressed (`mine`), and the
   * state carries that path — so a sibling view file in the same folder never
   * shows it, and a run that finishes after the pane moved to the sibling
   * still lands where it belongs. The FOLDER is the only thing `moved()`
   * watches (epoch): leaving it aborts the build and every write here; moving
   * to a sibling does not, because the folder — and the build's `dist/` — are
   * still what the pane is looking at, so the reload still happens. */
  const runDeploy = async () => {
    const started = epoch.current;
    const mine = path;
    const moved = () => epoch.current !== started;
    setDeploy({ path: mine, state: "working" });
    setCopied("idle");
    autoBuiltFor.current = folder;
    try {
      let hasBuild = false;
      if (folder !== "") {
        // `cancelRefetch: false`: a first read still in flight is JOINED, not
        // cancelled and re-issued — the default would have made "press Deploy
        // as the page opens" two manifest reads, the first thrown away.
        const fresh = await buildable.refetch({ cancelRefetch: false });
        if (moved()) return;
        hasBuild = hasBuildScript(fresh.data ?? "");
      }
      if (hasBuild) {
        const ok = await runBuild({ reload: false });
        if (moved()) return;
        if (!ok) {
          setDeploy({ path: mine, state: "failed", step: "build" });
          return;
        }
      }
      // The verdict's read IS the pane's next generation: the pane's own
      // query options, fetched into the cache under that key, then the pane
      // is pointed at it — a cache hit, so the frame shows what was verified
      // without a second read. `max`, not an absolute write: the generation
      // only ever moves forward, whatever else touched it.
      //
      // On failure the pane is NOT pointed at it: a query in error with no
      // data refetches on the key switch, and that second read — one the
      // verdict never saw — could succeed (a sandbox restore finishing) and
      // render the page directly under "the page does not open". The panel
      // carries the sentence; the pane keeps showing what it showed.
      const next = generationRef.current + 1;
      try {
        await queryClient.fetchQuery(wuiDocQuery(fs, folder, entry, mine, next));
      } catch (err) {
        if (moved()) return;
        setDeploy({
          path: mine,
          state: "failed",
          step: "open",
          // Only `WuiEntryMissing` carries a sentence written for a person;
          // any other throw here (the assembler, a base64 RangeError, a
          // cancelled query) is an internal message, and shown raw it is
          // "Maximum call stack size exceeded" in somebody's toolbar.
          why: err instanceof WuiEntryMissing ? err.message : "The page could not be opened.",
        });
        return;
      }
      if (moved()) return;
      setGeneration((g) => Math.max(g, next));
      setDeploy({ path: mine, state: "done", generation: next });
    } catch (err) {
      // Nothing above is expected to throw — but a run that did would leave
      // the button on "Deploying…" for the rest of the page's life, and a
      // loud failure needs a catcher. Named as what it is, not as a page
      // problem: a throw from the manifest read or the build stage sent the
      // publisher to look at a page that opened fine.
      if (moved()) return;
      // Logged, not shown: the message is internal (see the open branch).
      console.error("wui: deploy stopped unexpectedly", err);
      setDeploy({ path: mine, state: "failed", step: "unknown" });
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {author && (
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
          // Held while Deploy runs, like Rebuild: Deploy's verdict names a
          // generation, and a Refresh in between moved the pane past it.
          disabled={deploying}
          onClick={() => {
            // NOT the build log. Refresh after a failure is the reflex — you
            // fixed the file, now show me — and clearing it took the compiler
            // error, the only explanation on screen, along with the page.
            setGeneration((g) => g + 1);
          }}
        >
          Refresh
        </Btn>
        {canBuild && (
          <>
            {/* Held while Deploy runs, not only while a build does: Deploy has
                phases with no build in flight (the manifest re-read, the open
                check), and a Rebuild pressed in one of them started a second
                `pnpm run build` in the same folder beside Deploy's. */}
            <Btn size="sm" disabled={building || deploying} onClick={() => void runBuild()}>
              {building ? "Building…" : "Rebuild"}
            </Btn>
            {/* A switch, not a checkbox: flipping it takes effect at once, and
                a checkbox reads as a choice that has not happened yet. Short on
                screen, whole sentence on hover AND as the accessible name — the
                strip sits above someone else's page, so a sentence here is a
                sentence taken from them. */}
            <Switch
              checked={autoBuild}
              onChange={setAutoBuild}
              disabled={deploying}
              title="Rebuild this page whenever you open it"
            >
              Auto-rebuild
            </Switch>
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
        {/* Only where there is a slug to deploy under — like `callTool`, which
            is null without one. A host with none (a `view: wui` file opened in
            the KB IDE) used to draw it permanently disabled, saying nothing,
            over an address that would have read `…/w//…`. Disabled on
            `building` too: a Rebuild already in flight is the same build, and
            starting a second one over it is what the Rebuild button itself
            refuses. */}
        {slug && (
          <Btn
            size="sm"
            disabled={building || deploying}
            onClick={() => void runDeploy()}
            style={{ marginLeft: "auto" }}
          >
            {deploying ? "Deploying…" : "Deploy"}
          </Btn>
        )}
      </div>
      )}
      {author && deployHere.state !== "idle" && deployHere.state !== "working" && (
        <div
          role="status"
          style={{
            flex: "0 0 auto",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            padding: "8px 12px",
            borderBottom: "1px solid var(--paper-3)",
            fontSize: pxToRem(13),
          }}
        >
          {deployHere.state === "done" ? (
            <>
              <div style={{ fontWeight: 600 }}>✓ Deployed</div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                {/* Read-only and selectable: this IS the fallback when the
                    clipboard is unavailable (a non-secure context), so there is
                    one way to show the address, not two. */}
                <input
                  aria-label="Page address"
                  readOnly
                  value={address}
                  onFocus={(e) => e.currentTarget.select()}
                  style={{ flex: 1, minWidth: 0, font: "inherit", padding: "4px 6px" }}
                />
                <Btn size="sm" onClick={() => void copyAddress()}>
                  {copied === "done" ? "Copied" : copied === "failed" ? "Copy failed — select it" : "Copy"}
                </Btn>
                <a href={address} target="_blank" rel="noreferrer">
                  Open
                </a>
              </div>
              {/* P17's decision, spoken to the publisher: the address is a
                  shortcut, not a grant. */}
              <div style={{ color: "var(--text-paper-d)" }}>
                Anyone who can open this item can use this link.
              </div>
            </>
          ) : deployHere.step === "build" ? (
            <div style={{ color: "var(--err)" }}>Deploy failed — see the build output.</div>
          ) : deployHere.step === "open" ? (
            // The page's own sentence about why it does not open — the same
            // one the pane shows below — so the publisher is not sent to a
            // build log for a page that has no build.
            <div style={{ color: "var(--err)" }}>
              Deploy failed — the page does not open: {deployHere.why}
            </div>
          ) : (
            <div style={{ color: "var(--err)" }}>Deploy stopped unexpectedly — see the browser console.</div>
          )}
        </div>
      )}
      {/* No `author &&` here or on the reports pane: neither state can arise
          for a reader — no build is ever started on their account, and their
          reports are dropped at the listener — and a second gate that no test
          can turn red is a guard that only looks like one. */}
      {buildLog !== null && (
        <div
          style={{
            // The cap lives HERE, on the pane's flex item, because that is the
            // box with a definite height to take a percentage of. With it on the
            // log inside, the wrapper sized itself to the log's UNCLAMPED
            // content and then clamped the log against that — 270px of blank in
            // a 692px pane, with the page squeezed into a third of it.
            flex: "0 0 auto",
            display: "flex",
            flexDirection: "column",
            maxHeight: "30%",
            borderBottom: "1px solid var(--paper-3)",
          }}
        >
          <button
            type="button"
            onClick={() => setLogOpen((open) => !open)}
            aria-expanded={logOpen}
            // The control's name is what it DOES. Naming it after the status
            // line it carries made it a second button called "Building…",
            // indistinguishable from the one that starts a build.
            aria-label={logOpen ? "Hide build output" : "Show build output"}
            style={{
              flex: "0 0 auto",
              display: "flex",
              width: "100%",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 8,
              padding: "4px 8px",
              border: 0,
              background: "none",
              color: "var(--text-paper-d)",
              fontFamily: "var(--font-mono, ui-monospace, monospace)",
              fontSize: pxToRem(12),
              textAlign: "left",
              cursor: "pointer",
            }}
          >
            {/* A fixed word while it runs, not the newest line: the line is
                already on screen underneath, and two copies of it make the
                pane look like it is stuttering. */}
            <span
              // The one line that IS worth announcing: it changes twice a build
              // and it carries the verdict. The log itself is `aria-live="off"`
              // precisely so this can be heard.
              aria-live="polite"
              style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {building ? "Building…" : logOpen ? "Build output" : logSummary}
            </span>
            <span style={{ flex: "0 0 auto", opacity: 0.7 }}>{logOpen ? "Hide" : "Show"}</span>
          </button>
          {logOpen && (
            // The build's own words, verbatim and monospaced, because they are
            // a compiler's and their columns mean something.
            <div
              ref={logRef}
              role="log"
              aria-label="Build output"
              // Off, deliberately. The pane already has one polite live region
              // — the reports panel, which speaks rarely and about something
              // the reader must act on. A build emits a chunk every few
              // milliseconds, and announcing each one drowns the other. The
              // outcome is announced instead: it lands in the summary line.
              aria-live="off"
              style={{
                // Takes what the strip has left, and scrolls. `minHeight: 0`
                // because a flex item will not shrink below its content without
                // it, which puts the overflow straight back on the pane.
                flex: "1 1 auto",
                minHeight: 0,
                overflowY: "auto",
                padding: "0 8px 6px",
                fontFamily: "var(--font-mono, ui-monospace, monospace)",
                fontSize: pxToRem(12),
                whiteSpace: "pre-wrap",
                color: "var(--text-paper-d)",
              }}
            >
              {buildLog.length === 0 ? "Starting the build…" : logText}
            </div>
          )}
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
      ) : firstBuild ? (
        // Whatever the folder holds right now is about to be replaced by what
        // the build produces, and mid-restore it may not even read correctly —
        // so this waits rather than showing a page with a shelf life of
        // seconds.
        <div role="status" style={{ padding: 12, color: "var(--text-paper-d)" }}>
          Building… the page appears when this finishes.
        </div>
      ) : built.error && building ? (
        // The first open of a page nobody has built yet: `dist/` really is
        // absent, and saying so in red — under a log showing the build that is
        // about to create it — is alarming and, seconds later, untrue.
        <div role="status" style={{ padding: 12, color: "var(--text-paper-d)" }}>
          Building… the page appears when this finishes.
        </div>
      ) : built.error instanceof WuiEntryMissing && built.error.kind === "absent" && !author ? (
        // A reader followed a link to a page nobody has built (or one whose
        // entry is gone). They cannot rebuild it and did not choose the file,
        // so the sentence names the page's STATE, not the missing file — a
        // blank frame here reads as a broken page rather than an unpublished
        // one. Not red: nothing they did is wrong. ONLY for a genuine absence:
        // a read that failed, or an entry that is not HTML, carries its own
        // reason below — told "not published", the reader reports that to the
        // author, who re-deploys a page that was fine.
        //
        // Tentative, and with a way back: on this platform "not there" is
        // what a read answers while the item's sandbox is still being
        // restored (`_warm` does not wait on `.ready`), so a published page
        // can answer 404 for a moment. "Try again" is a re-read of the folder
        // — never a build — the same thing Refresh does for an author.
        <div
          role="status"
          style={{ padding: 12, color: "var(--text-paper-d)", display: "flex", gap: 8, alignItems: "center" }}
        >
          <span>
            This page has not been published yet — or it is still being restored. Try again in a moment.
          </span>
          <Btn size="sm" onClick={() => setGeneration((g) => g + 1)}>
            Try again
          </Btn>
        </div>
      ) : built.error ? (
        // Plain language and the file's name: whoever hits this may have no
        // console to open, and this text is what they forward to the agent.
        // A reader gets the same way back as on the "not published" branch —
        // a 503 mid-restore or a dropped connection is as transient as the
        // 404 is, and only one of them offering Try again made no sense.
        <div
          role="status"
          style={{ padding: 12, color: "var(--err)", display: "flex", gap: 8, alignItems: "center" }}
        >
          <span>{built.error instanceof Error ? built.error.message : "This WUI could not be opened."}</span>
          {!author && (
            <Btn size="sm" onClick={() => setGeneration((g) => g + 1)}>
              Try again
            </Btn>
          )}
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

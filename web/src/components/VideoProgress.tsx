/**
 * VideoProgress (plan-chat-video-export P8) — the line under the chat header
 * that follows one video job, by watching ONE file: `<output>.progress.json`.
 *
 * There is no run model and no status route (decisions 11–12). The worker
 * rewrites the file every heartbeat with the stage and the elapsed time; on
 * success it deletes it; on failure it leaves it with the sentence. So this
 * component polls the ordinary file route and reads the three endings off
 * the file itself:
 *
 * - a `failed` stage: the worker's sentence, and Dismiss removes the file
 *   the worker left for a person to read;
 * - the file GONE and the output there: done — the video is in the tree;
 * - the file GONE and no output: cancelled — by the Cancel here, or by
 *   deleting the file in the tree (the documented way; decision 12). The
 *   worker notices at its next heartbeat and writes nothing. Reading "gone
 *   without my Cancel" as done put "影片已存到 … [開啟]" over a video that
 *   was never made.
 *
 * Cancel and Dismiss go through `DELETE …/chat-video?path=`, which lets the
 * REQUESTER delete the job's file — the plain file DELETE needs
 * `edit_content`, which `add_content` (the verb that started the video)
 * does not include. A refusal is shown, not swallowed.
 *
 * Polling backs off from a second to eight (`pollDelay`): a 41-second
 * render is not improved by a request every second for all of it.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api";
import { type ChatVideoProgress, type ChatVideoQueued, cancelChatVideo } from "../api/chatVideo";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import type { FileContent } from "../api/types";
import { useOpenFile } from "../hooks/openFile";
import { useT } from "../lib/i18n";
import { relPath } from "../lib/relPath";

export type VideoProgressClient = {
  readFile: (slug: string, itemId: string, path: string) => Promise<FileContent>;
  /** Whether the OUTPUT is there once the progress file is gone. */
  exists: (slug: string, itemId: string, path: string) => Promise<boolean>;
  /** Delete the progress file as its requester (`DELETE …/chat-video?path=`). */
  cancel: (slug: string, itemId: string, progressPath: string) => Promise<void>;
};

const realClient: VideoProgressClient = {
  readFile: (slug, itemId, path) => api.readFile(slug, itemId, path),
  exists: (slug, itemId, path) => api.fileExists(slug, itemId, path),
  cancel: cancelChatVideo,
};

/** Milliseconds until the next look at the file, by how many looks so far:
 * a second for the first five, then doubling to a cap of eight seconds. */
export function pollDelay(polls: number): number {
  if (polls < 5) return 1000;
  return Math.min(8000, 1000 * 2 ** (polls - 4));
}

/** What the file said and WHEN it was read, or that it is gone — and then
 * whether the output is there, which is what tells done from cancelled.
 * A file that is not this job's — another job's token (a cancel, then a
 * new request for the same output) or not our document at all (a
 * hand-edited file) — reads as gone: the worker's own `mine()` rule.
 * `readAt` is what the heartbeat's age is measured from: a file nobody
 * rewrites is byte-identical on every poll, and the query's structural
 * sharing would otherwise hand back the same object and the component
 * would never re-render to notice the rule had passed. */
type Reading =
  | { kind: "progress"; progress: ChatVideoProgress; readAt: number }
  | { kind: "gone"; outputExists: boolean };

/** The file's text as this job's progress, or null when it is not ours. */
function ours(text: string, token: string): ChatVideoProgress | null {
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed === "object" && parsed !== null && (parsed as { token?: unknown }).token === token) {
      return parsed as ChatVideoProgress;
    }
  } catch {
    // not JSON: not our document
  }
  return null;
}

type Props = {
  slug: string;
  itemId: string;
  job: ChatVideoQueued;
  onDismiss: () => void;
  client?: VideoProgressClient;
  poll?: (polls: number) => number;
};

/** One pill per JOB: the body is keyed on the job's token, so a second
 * video queued while the pill still shows the first's ending starts with
 * fresh state. Without the key the instance was reused — `cancelling`
 * stayed true (B's Cancel disabled after A's cancel), `refetched` stayed
 * true (B's done never refetched the tree), and the poll count carried
 * over (B's first re-poll waited the 8-second cap). */
export function VideoProgress(props: Props) {
  return <VideoProgressBody key={props.job.token} {...props} />;
}

function VideoProgressBody({
  slug,
  itemId,
  job,
  onDismiss,
  client = realClient,
  poll = pollDelay,
}: Props) {
  const t = useT();
  const queryClient = useQueryClient();
  const openFile = useOpenFile();
  const polls = useRef(0);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const refetched = useRef(false);

  const reading = useQuery<Reading>({
    // The token is in the key: a second job at the SAME path (the default
    // name is to the second; a script can repeat one) must not open on the
    // first job's last reading — `gcTime: 0` is a `setTimeout(0)`, and the
    // new body subscribes in the same commit, before it fires.
    queryKey: qk.chatVideoProgress(itemId, job.progress_path, job.token),
    queryFn: async () => {
      polls.current += 1;
      try {
        const file = await client.readFile(slug, itemId, job.progress_path);
        const progress = file.kind === "text" ? ours(file.text, job.token) : null;
        if (progress !== null) return { kind: "progress", progress, readAt: Date.now() };
      } catch (e) {
        if (!(e instanceof HttpError && e.status === 404)) throw e;
      }
      return { kind: "gone", outputExists: await client.exists(slug, itemId, job.output_path) };
    },
    refetchInterval: (q) => {
      const d = q.state.data;
      if (d?.kind === "gone" || d?.progress.stage === "failed") return false;
      return poll(polls.current);
    },
    staleTime: 0,
    gcTime: 0,
  });

  const data = reading.data;
  const gone = data?.kind === "gone";
  // The plan's data flow: the file gone AND the output there is done;
  // gone with no output is cancelled — whoever deleted the file.
  const done = gone && data.outputExists;
  if (done && !refetched.current) {
    // The video is in the tree now; the worker cannot tell this pod, so the
    // watcher does what a `FileChanged` would have.
    refetched.current = true;
    void queryClient.invalidateQueries({ queryKey: qk.files(itemId) });
  }

  const removeFile = async (): Promise<boolean> => {
    setCancelError(null);
    try {
      await client.cancel(slug, itemId, job.progress_path);
      return true;
    } catch (e) {
      // A refusal (the requester is someone else and this viewer may not
      // edit) is shown; "already gone" is not an error the client throws.
      setCancelError(e instanceof Error ? e.message : String(e));
      return false;
    }
  };

  if (done) {
    return (
      <div className="video-progress" role="status" data-testid="video-progress" data-state="done">
        <span className="video-progress__saved">
          <span>{t("video.progress.done", { path: "" })}</span>
          {/* `relPath`: with `direction: rtl` (the left-ellipsis trick) a
              leading "/" is a neutral the bidi algorithm moves to the end. */}
          <span className="video-progress__path" title={job.output_path}>
            {relPath(job.output_path)}
          </span>
        </span>
        {openFile && (
          <button
            type="button"
            className="btn"
            data-variant="primary"
            data-size="sm"
            data-testid="video-progress-open"
            onClick={() => openFile(job.output_path)}
          >
            {t("video.progress.open")}
          </button>
        )}
        <a
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="video-progress-download"
          href={api.fileContentUrl(slug, itemId, job.output_path)}
          download={relPath(job.output_path).split("/").pop()}
        >
          {t("video.progress.download")}
        </a>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="video-progress-dismiss"
          onClick={onDismiss}
        >
          {t("video.progress.dismiss")}
        </button>
      </div>
    );
  }

  if (gone) {
    return (
      <div className="video-progress" role="status" data-testid="video-progress" data-state="cancelled">
        <span>{t("video.progress.cancelled")}</span>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="video-progress-dismiss"
          onClick={onDismiss}
        >
          {t("video.progress.dismiss")}
        </button>
      </div>
    );
  }

  if (data?.kind === "progress" && data.progress.stage === "failed") {
    return (
      <div className="video-progress" role="alert" data-testid="video-progress" data-state="failed">
        <span>{t("video.progress.failed", { error: data.progress.error })}</span>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="video-progress-dismiss"
          onClick={() => {
            void removeFile().then((ok) => ok && onDismiss());
          }}
        >
          {t("video.progress.dismiss")}
        </button>
        {cancelError && <span className="video-progress__stale">{cancelError}</span>}
      </div>
    );
  }

  const progress = data?.kind === "progress" ? data.progress : null;
  const stage = progress?.stage ?? "queued";
  const expected = progress?.expected_seconds ?? job.expected_seconds;
  const elapsed = progress?.elapsed_seconds ?? 0;
  const fraction = expected > 0 ? Math.min(1, elapsed / expected) : 0;
  // The server's own liveness rule (`progress.is_alive`): a RUNNING stage
  // whose heartbeat is older than `stale_after_seconds` has no worker on it
  // — the runbook's symptom for a deployment that forgot the chat-video
  // worker. A `queued` file has no heartbeat to judge: nobody rewrites it
  // while the job waits in line, and with one worker and renders of a
  // minute or more the line is the normal case — so past the rule it says
  // what is known (nobody has taken it yet), not "no worker".
  const silentFor =
    data?.kind === "progress"
      ? Math.round((data.readAt - Date.parse(data.progress.heartbeat_at)) / 1000)
      : 0;
  const pastRule = progress !== null && silentFor > job.stale_after_seconds;
  const waiting = pastRule && stage === "queued";
  const stale = pastRule && stage !== "queued";
  return (
    <div
      className="video-progress"
      role="status"
      data-testid="video-progress"
      data-state={stale ? "stale" : waiting ? "waiting" : "running"}
    >
      <span className="video-progress__stage">{t(`video.progress.${stage}`)}</span>
      <span className="video-progress__elapsed">
        {t("video.progress.elapsed", { e: elapsed, x: expected })}
      </span>
      {stale && (
        <span className="video-progress__stale">{t("video.progress.stale", { n: silentFor })}</span>
      )}
      {waiting && (
        <span className="video-progress__waiting">
          {t("video.progress.waiting", { n: silentFor })}
        </span>
      )}
      {cancelError && <span className="video-progress__stale">{cancelError}</span>}
      <span className="video-progress__bar" aria-hidden="true">
        <span className="video-progress__fill" style={{ width: `${Math.round(fraction * 100)}%` }} />
      </span>
      <button
        type="button"
        className="btn"
        data-variant="secondary"
        data-size="sm"
        data-testid="video-progress-cancel"
        disabled={cancelling}
        onClick={() => {
          setCancelling(true);
          void removeFile().then((ok) => {
            if (!ok) setCancelling(false); // refused: the button comes back with the reason
          });
        }}
      >
        {t("video.progress.cancel")}
      </button>
    </div>
  );
}

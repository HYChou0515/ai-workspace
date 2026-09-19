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
 * - the file GONE without a Cancel: done — the video is in the tree;
 * - the file GONE after a Cancel: cancelled. Cancel IS deleting the file;
 *   the worker notices at its next heartbeat and writes nothing.
 *
 * Polling backs off from a second to eight (`pollDelay`): a 41-second
 * render is not improved by a request every second for all of it.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api } from "../api";
import type { ChatVideoProgress, ChatVideoQueued } from "../api/chatVideo";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import type { FileContent } from "../api/types";
import { useOpenFile } from "../hooks/openFile";
import { useT } from "../lib/i18n";
import { relPath } from "../lib/relPath";

export type VideoProgressClient = {
  readFile: (slug: string, itemId: string, path: string) => Promise<FileContent>;
  deleteFile: (slug: string, itemId: string, path: string) => Promise<void>;
};

const realClient: VideoProgressClient = {
  readFile: (slug, itemId, path) => api.readFile(slug, itemId, path),
  deleteFile: (slug, itemId, path) => api.deleteFile(slug, itemId, path),
};

/** Milliseconds until the next look at the file, by how many looks so far:
 * a second for the first five, then doubling to a cap of eight seconds. */
export function pollDelay(polls: number): number {
  if (polls < 5) return 1000;
  return Math.min(8000, 1000 * 2 ** (polls - 4));
}

/** What the file said, or that it is gone. Anything that is not our
 * document (a hand-edited file) reads as gone too — nothing to follow. */
type Reading = { kind: "progress"; progress: ChatVideoProgress } | { kind: "gone" };

export function VideoProgress({
  slug,
  itemId,
  job,
  onDismiss,
  client = realClient,
  poll = pollDelay,
}: {
  slug: string;
  itemId: string;
  job: ChatVideoQueued;
  onDismiss: () => void;
  client?: VideoProgressClient;
  poll?: (polls: number) => number;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const openFile = useOpenFile();
  const polls = useRef(0);
  const [cancelled, setCancelled] = useState(false);
  const refetched = useRef(false);

  const reading = useQuery<Reading>({
    queryKey: qk.chatVideoProgress(itemId, job.progress_path),
    queryFn: async () => {
      polls.current += 1;
      try {
        const file = await client.readFile(slug, itemId, job.progress_path);
        if (file.kind !== "text") return { kind: "gone" };
        return { kind: "progress", progress: JSON.parse(file.text) as ChatVideoProgress };
      } catch (e) {
        if (e instanceof HttpError && e.status === 404) return { kind: "gone" };
        throw e;
      }
    },
    refetchInterval: (q) => {
      const d = q.state.data;
      if (d?.kind === "gone" || d?.progress.stage === "failed") return false;
      return poll(polls.current);
    },
    // The first look is answered from the network, never from a cache of
    // the previous job at the same path.
    staleTime: 0,
    gcTime: 0,
  });

  const data = reading.data;
  const gone = data?.kind === "gone";
  // A `failed` file is never rewritten and polling stops on it, so the only
  // way it goes away is the Dismiss below, which unmounts this. Absence is
  // therefore one of two things: done, or the Cancel this component sent.
  const done = gone && !cancelled;
  if (done && !refetched.current) {
    // The video is in the tree now; the worker cannot tell this pod, so the
    // watcher does what a `FileChanged` would have.
    refetched.current = true;
    void queryClient.invalidateQueries({ queryKey: qk.files(itemId) });
  }

  const removeFile = async () => {
    try {
      await client.deleteFile(slug, itemId, job.progress_path);
    } catch {
      // Already gone: the worker deleted it between our last look and this click.
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

  if (gone && cancelled) {
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
            void removeFile().then(onDismiss);
          }}
        >
          {t("video.progress.dismiss")}
        </button>
      </div>
    );
  }

  const progress = data?.kind === "progress" ? data.progress : null;
  const stage = progress?.stage ?? "queued";
  const expected = progress?.expected_seconds ?? job.expected_seconds;
  const elapsed = progress?.elapsed_seconds ?? 0;
  const fraction = expected > 0 ? Math.min(1, elapsed / expected) : 0;
  // The server's own liveness rule (`progress.is_alive`): a heartbeat older
  // than `stale_after_seconds` means no worker holds this file — the runbook's
  // symptom for a deployment that forgot the chat-video worker.
  const silentFor = progress
    ? Math.round((Date.now() - Date.parse(progress.heartbeat_at)) / 1000)
    : 0;
  const stale = progress !== null && silentFor > job.stale_after_seconds;
  return (
    <div
      className="video-progress"
      role="status"
      data-testid="video-progress"
      data-state={stale ? "stale" : "running"}
    >
      <span className="video-progress__stage">{t(`video.progress.${stage}`)}</span>
      <span className="video-progress__elapsed">
        {t("video.progress.elapsed", { e: elapsed, x: expected })}
      </span>
      {stale && (
        <span className="video-progress__stale">{t("video.progress.stale", { n: silentFor })}</span>
      )}
      <span className="video-progress__bar" aria-hidden="true">
        <span className="video-progress__fill" style={{ width: `${Math.round(fraction * 100)}%` }} />
      </span>
      <button
        type="button"
        className="btn"
        data-variant="secondary"
        data-size="sm"
        data-testid="video-progress-cancel"
        disabled={cancelled}
        onClick={() => {
          setCancelled(true);
          void removeFile();
        }}
      >
        {t("video.progress.cancel")}
      </button>
    </div>
  );
}

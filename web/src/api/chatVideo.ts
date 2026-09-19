/**
 * The chat-video API (plan-chat-video-export P6/P7): queue a video of a
 * transcript, and the ceilings the form must stay within. The transcript the
 * dialog sends is the export route's own JSON, sliced on this side — so the
 * FE drives exactly the API a script would (`docs/chat-video.md`).
 */

import { apiFetch, detailSentence } from "./http";
import { fetchChatExport } from "./workflows";

/** The `.chat.json` document — `title` + `messages` in server (oldest-first)
 * order. Messages are kept opaque: the dialog only counts and labels them. */
export type ChatTranscript = {
  title: string;
  messages: Array<{ role: string; content: string; [k: string]: unknown }>;
};

/** `VideoOptions` fields, any subset (the server fills the rest). */
export type ChatVideoOptions = {
  width?: number;
  height?: number;
  scale?: number;
  zoom?: number;
  zoom_ms?: number;
  type_ms?: number;
  stream_ms?: number;
  tool_pause_ms?: number;
  speed?: number;
  max_seconds?: number;
  tool_output_chars?: number;
  fmt?: string[];
};

export type ChatVideoRequest = {
  transcript: ChatTranscript;
  options: ChatVideoOptions;
  /** `null` / absent: the server names it `/exports/chat-video/<title>-<ts>.<fmt>`. */
  output_path?: string | null;
};

export type ChatVideoQueued = {
  output_path: string;
  source_path: string;
  progress_path: string;
  expected_seconds: number;
  /** The server's rule for a worker that stopped breathing: a heartbeat
   * older than this means nobody is on the job. */
  stale_after_seconds: number;
};

export type ChatVideoLimits = {
  max_pixels: number;
  max_seconds: number;
  max_output_bytes: number;
};

/** The progress file the worker keeps beside the output (`chat_video/progress.py`). */
export type ChatVideoProgress = {
  stage: "queued" | "rendering" | "encoding" | "failed";
  expected_seconds: number;
  elapsed_seconds: number;
  started_at: string;
  heartbeat_at: string;
  output_path: string;
  requested_by: string;
  error: string;
};

const base = (slug: string, itemId: string) =>
  `/a/${encodeURIComponent(slug)}/items/${encodeURIComponent(itemId)}`;

/** The whole thread as the export document — what the range picker counts
 * and what the video is sliced from. */
export async function fetchChatTranscript(
  slug: string,
  itemId: string,
  chatId: string,
): Promise<ChatTranscript> {
  const { blob } = await fetchChatExport(slug, itemId, chatId, { format: "json", range: null });
  return JSON.parse(await blob.text()) as ChatTranscript;
}

export async function startChatVideo(
  slug: string,
  itemId: string,
  body: ChatVideoRequest,
): Promise<ChatVideoQueued> {
  const res = await apiFetch(`${base(slug, itemId)}/chat-video`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // The server's sentence is the whole explanation (409 "already being
    // made at …", 422 "1921×1080 is … pixels; at most …"); the status alone
    // would send the person guessing.
    throw new Error((await detailSentence(res)) || `chat-video failed: ${res.status}`);
  }
  return (await res.json()) as ChatVideoQueued;
}

export async function fetchChatVideoLimits(slug: string, itemId: string): Promise<ChatVideoLimits> {
  const res = await apiFetch(`${base(slug, itemId)}/chat-video`);
  if (!res.ok) throw new Error(`chat-video limits failed: ${res.status}`);
  return (await res.json()) as ChatVideoLimits;
}

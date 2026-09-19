// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatVideoProgress, ChatVideoQueued } from "../api/chatVideo";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import { OpenFileProvider } from "../hooks/openFile";
import { renderWithQuery } from "../test/queryWrapper";
import { pollDelay, VideoProgress, type VideoProgressClient } from "./VideoProgress";

const JOB: ChatVideoQueued = {
  output_path: "/exports/chat-video/OOM-1.mp4",
  source_path: "/exports/chat-video/OOM-1.mp4.chat.json",
  progress_path: "/exports/chat-video/OOM-1.mp4.progress.json",
  expected_seconds: 41,
  stale_after_seconds: 60,
};

function progress(over: Partial<ChatVideoProgress>): ChatVideoProgress {
  return {
    stage: "rendering",
    expected_seconds: 41,
    elapsed_seconds: 12,
    started_at: "2026-09-19T03:10:00Z",
    heartbeat_at: "2026-09-19T03:10:12Z",
    output_path: JOB.output_path,
    requested_by: "bob",
    error: "",
    ...over,
  };
}

/** A progress file that answers a scripted sequence, then repeats the last. */
function fileThat(answers: Array<ChatVideoProgress | "gone">): VideoProgressClient {
  let i = 0;
  return {
    readFile: vi.fn(async (_slug: string, _itemId: string, _path: string) => {
      const a = answers[Math.min(i++, answers.length - 1)];
      if (a === "gone") throw new HttpError(404, "read failed: 404");
      return { kind: "text" as const, path: JOB.progress_path, size: 1, text: JSON.stringify(a), encoding: "utf-8" as const };
    }),
    deleteFile: vi.fn(async () => {}),
  };
}

function mount(client: VideoProgressClient, openFile?: (path: string) => void) {
  const onDismiss = vi.fn();
  const ui = (
    // `poll`: the real backoff starts at a second; the tests need the next
    // answer now, and `pollDelay` has its own test below.
    <VideoProgress
      slug="rca"
      itemId="rca:1"
      job={JOB}
      onDismiss={onDismiss}
      client={client}
      poll={() => 20}
    />
  );
  const rendered = renderWithQuery(
    openFile ? <OpenFileProvider value={openFile}>{ui}</OpenFileProvider> : ui,
  );
  return { onDismiss, ...rendered };
}

afterEach(() => cleanup());

describe("VideoProgress — three endings for one file", () => {
  it("shows the stage and the elapsed time while the worker is at it", async () => {
    mount(fileThat([progress({ stage: "rendering", elapsed_seconds: 12 })]));

    await screen.findByText("錄影中");
    expect(screen.getByText("12 / 約 41 秒")).toBeTruthy();
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "running");
  });

  it("the file going away is DONE: the path, an Open, and the file tree refetched", async () => {
    const openFile = vi.fn();
    const client_ = fileThat([progress({}), "gone"]);
    const { client, onDismiss } = mount(client_, openFile);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await screen.findByText(/影片已存到/);
    // Shown without the leading slash (the left-ellipsis needs rtl, which
    // would move a leading "/" to the end); the full path is the title.
    expect(screen.getByText("exports/chat-video/OOM-1.mp4")).toHaveAttribute(
      "title",
      JOB.output_path,
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.files("rca:1") });
    // …and the file is not asked about again: a finished job is not polled.
    const looks = (client_.readFile as ReturnType<typeof vi.fn>).mock.calls.length;
    await new Promise((r) => setTimeout(r, 80));
    expect((client_.readFile as ReturnType<typeof vi.fn>).mock.calls.length).toBe(looks);
    fireEvent.click(screen.getByTestId("video-progress-open"));
    expect(openFile).toHaveBeenCalledWith(JOB.output_path);
    fireEvent.click(screen.getByTestId("video-progress-dismiss"));
    expect(onDismiss).toHaveBeenCalled();
  });

  it("a failed stage shows the worker's sentence; Dismiss removes the file it left", async () => {
    const c = fileThat([progress({ stage: "failed", error: "the mp4 is 101 bytes; at most 100" })]);
    const { onDismiss } = mount(c);

    await screen.findByText(/the mp4 is 101 bytes; at most 100/);
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "failed");
    fireEvent.click(screen.getByTestId("video-progress-dismiss"));

    await waitFor(() => expect(c.deleteFile).toHaveBeenCalledWith("rca", "rca:1", JOB.progress_path));
    expect(onDismiss).toHaveBeenCalled();
  });

  it("Cancel deletes the progress file, and its absence then reads as cancelled, not done", async () => {
    const c = fileThat([progress({}), progress({}), "gone"]);
    mount(c);
    await screen.findByText("錄影中");

    fireEvent.click(screen.getByTestId("video-progress-cancel"));

    await waitFor(() => expect(c.deleteFile).toHaveBeenCalledWith("rca", "rca:1", JOB.progress_path));
    await screen.findByText("影片已取消");
    expect(screen.queryByText(/影片已存到/)).toBeNull();
  });
});

describe("VideoProgress — no worker", () => {
  it("a heartbeat older than the server's rule reads as no worker, by the server's number", async () => {
    // The runbook's symptom for a missing `rca-worker-chat-video`: the file
    // stays `queued` and nobody rewrites it. Without this line the person
    // watches "queued 0 / 41 s" for ever.
    const old = new Date(Date.now() - 5 * 60_000).toISOString();
    mount(fileThat([progress({ stage: "queued", elapsed_seconds: 0, heartbeat_at: old })]));

    await screen.findByText(/worker 沒有回應/);
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "stale");
    // Cancel is still there: deleting the file is how this one is cleared.
    expect(screen.getByTestId("video-progress-cancel")).toBeTruthy();
  });

  it("a heartbeat within the rule is an ordinary wait", async () => {
    const fresh = new Date(Date.now() - 20_000).toISOString();
    mount(fileThat([progress({ stage: "queued", elapsed_seconds: 0, heartbeat_at: fresh })]));

    await screen.findByText("影片排隊中");
    expect(screen.queryByText(/worker 沒有回應/)).toBeNull();
  });
});

describe("pollDelay — a second at first, backing off to eight", () => {
  it("is 1 s for the first five polls, then doubles to a cap", () => {
    expect([0, 4, 5, 6, 7, 8, 20].map(pollDelay)).toEqual([1000, 1000, 2000, 4000, 8000, 8000, 8000]);
  });
});

// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatVideoProgress, ChatVideoQueued } from "../api/chatVideo";
import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import { OpenFileProvider } from "../hooks/openFile";
import { DialogProvider } from "./Dialog";
import { renderWithQuery } from "../test/queryWrapper";
import { pollDelay, VideoProgress, type VideoProgressClient } from "./VideoProgress";

const JOB: ChatVideoQueued = {
  output_path: "/exports/chat-video/OOM-1.mp4",
  source_path: "/exports/chat-video/OOM-1.mp4.chat.json",
  progress_path: "/exports/chat-video/OOM-1.mp4.progress.json",
  expected_seconds: 41,
  stale_after_seconds: 60,
  token: "job-1",
};

function progress(over: Partial<ChatVideoProgress>): ChatVideoProgress {
  return {
    stage: "rendering",
    expected_seconds: 41,
    elapsed_seconds: 12,
    started_at: new Date(Date.now() - 12_000).toISOString(),
    // From the clock, never a literal: a fixed timestamp made this fixture
    // "stale" sixty seconds after it was written, and the suite red for good.
    heartbeat_at: new Date().toISOString(),
    output_path: JOB.output_path,
    requested_by: "bob",
    error: "",
    token: JOB.token,
    ...over,
  };
}

/** A progress file that answers a scripted sequence, then repeats the last;
 * `outputExists` is what the output path answers once the file is gone.
 * `{ raw }` is the file's text as is — a hand-edited file. */
function fileThat(
  answers: Array<ChatVideoProgress | { raw: string } | "gone">,
  { outputExists = true }: { outputExists?: boolean } = {},
): VideoProgressClient {
  let i = 0;
  return {
    readFile: vi.fn(async (_slug: string, _itemId: string, _path: string) => {
      const a = answers[Math.min(i++, answers.length - 1)];
      if (a === "gone") throw new HttpError(404, "read failed: 404");
      const text = "raw" in a ? a.raw : JSON.stringify(a);
      return { kind: "text" as const, path: JOB.progress_path, size: 1, text, encoding: "utf-8" as const };
    }),
    exists: vi.fn(async () => outputExists),
    cancel: vi.fn(async () => {}),
  };
}

const looks = (c: VideoProgressClient) => (c.readFile as ReturnType<typeof vi.fn>).mock.calls.length;

function pill(client: VideoProgressClient, job: ChatVideoQueued, onDismiss: () => void) {
  // `poll`: the real backoff starts at a second; the tests need the next
  // answer now, and `pollDelay` has its own test below.
  return (
    <VideoProgress
      slug="rca"
      itemId="rca:1"
      job={job}
      onDismiss={onDismiss}
      client={client}
      poll={() => 20}
    />
  );
}

function mount(
  client: VideoProgressClient,
  openFile?: (path: string) => void,
  job: ChatVideoQueued = JOB,
) {
  const onDismiss = vi.fn();
  const ui = pill(client, job, onDismiss);
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

  it("the file going away WITH the output present is DONE: the path, Open, Download, the tree refetched", async () => {
    const openFile = vi.fn();
    const client_ = fileThat([progress({}), "gone"], { outputExists: true });
    const { client, onDismiss } = mount(client_, openFile);
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await screen.findByText(/影片已存到/);
    expect(client_.exists).toHaveBeenCalledWith("rca", "rca:1", JOB.output_path);
    expect(screen.getByTestId("video-progress-download")).toHaveAttribute(
      "href",
      expect.stringContaining("/files/exports/chat-video/OOM-1.mp4"),
    );
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
    // A failed file is final: the worker will not rewrite it, so it is not
    // asked about again (the reason `sawFailed` could go: "gone after
    // failed" is unreachable while this holds).
    const n = looks(c);
    await new Promise((r) => setTimeout(r, 80));
    expect(looks(c)).toBe(n);
    fireEvent.click(screen.getByTestId("video-progress-dismiss"));

    await waitFor(() => expect(c.cancel).toHaveBeenCalledWith("rca", "rca:1", JOB.progress_path));
    expect(onDismiss).toHaveBeenCalled();
  });

  it("Cancel deletes the progress file through the route; absence with no output is cancelled", async () => {
    const c = fileThat([progress({}), progress({}), "gone"], { outputExists: false });
    mount(c);
    await screen.findByText("錄影中");

    fireEvent.click(screen.getByTestId("video-progress-cancel"));

    await waitFor(() => expect(c.cancel).toHaveBeenCalledWith("rca", "rca:1", JOB.progress_path));
    await screen.findByText("影片已取消");
    expect(screen.queryByText(/影片已存到/)).toBeNull();
  });

  it("the file deleted from the TREE (the documented cancel) is cancelled too — not \"saved\"", async () => {
    // The plan's data flow: gone AND the output exists → saved. The first
    // version read gone + "I did not press Cancel" as saved, so deleting the
    // file in the tree showed "影片已存到 … [開啟]" over a video that was
    // never made.
    const c = fileThat([progress({}), "gone"], { outputExists: false });
    mount(c);

    await screen.findByText("影片已取消");
    expect(screen.queryByText(/影片已存到/)).toBeNull();
  });

  it("a file that is not this job's — another token, or not our document — is gone to this watcher", async () => {
    // The worker's own rule (`mine()`): a cancel, then a new request for
    // the same output, puts ANOTHER job's file at the path. Following it
    // would show that job's progress under this job's name. A hand-edited
    // file is the same case: not ours. Both stop the polling.
    const theirs = fileThat([progress({}), progress({ token: "job-2" })], { outputExists: false });
    mount(theirs);
    await screen.findByText("影片已取消");
    const n = looks(theirs);
    await new Promise((r) => setTimeout(r, 80));
    expect(looks(theirs)).toBe(n);
    cleanup();

    const edited = fileThat([{ raw: "not json at all" }], { outputExists: true });
    mount(edited);
    await screen.findByText(/影片已存到/);
    const m = looks(edited);
    await new Promise((r) => setTimeout(r, 80));
    expect(looks(edited)).toBe(m);
  });

  it("a cancel the server refuses is shown, not swallowed", async () => {
    const c = fileThat([progress({})]);
    (c.cancel as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("forbidden: edit_content"));
    mount(c);
    await screen.findByText("錄影中");

    fireEvent.click(screen.getByTestId("video-progress-cancel"));

    await screen.findByText(/forbidden: edit_content/);
    expect(screen.getByTestId("video-progress-cancel")).not.toBeDisabled();
  });
});

describe("VideoProgress — one pill per job", () => {
  it("a second job handed to the same pill starts fresh: its Cancel works and its done refetches the tree", async () => {
    // The mount site keeps the pill up through the first job's ending and
    // the Export action stays available, so job B arrives as a new `job`
    // prop. The instance used to be reused: `cancelling` from A's cancel
    // left B's Cancel disabled, and `refetched` from A's done left B's
    // done without the tree refetch. The body is keyed on the token.
    const a = fileThat([progress({}), "gone"], { outputExists: false });
    const { rerender, onDismiss, client } = mount(a);
    await screen.findByText("錄影中");
    fireEvent.click(screen.getByTestId("video-progress-cancel"));
    await screen.findByText("影片已取消");

    const jobB = { ...JOB, output_path: "/exports/b.mp4", progress_path: "/exports/b.mp4.progress.json", token: "job-2" };
    const b = fileThat([progress({ token: "job-2" }), progress({ token: "job-2" }), "gone"], { outputExists: true });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    // `rerender` replaces the whole tree, providers included.
    rerender(
      <QueryClientProvider client={client}>
        <DialogProvider>{pill(b, jobB, onDismiss)}</DialogProvider>
      </QueryClientProvider>,
    );

    await screen.findByText("錄影中");
    expect(screen.getByTestId("video-progress-cancel")).not.toBeDisabled();
    await screen.findByText(/影片已存到/);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.files("rca:1") });
  });
});

describe("VideoProgress — waiting and no worker", () => {
  it("a queued file older than the rule is WAITING, not \"no worker\": nobody rewrites a queued file", async () => {
    // One worker renders one job at a time and a render is a minute or
    // more, so a job in line normally passes 60 s untouched. The sentence
    // says what is known: nobody has taken it yet — the worker may be busy,
    // or there may be none. Cancel stays.
    const old = new Date(Date.now() - 5 * 60_000).toISOString();
    mount(fileThat([progress({ stage: "queued", elapsed_seconds: 0, heartbeat_at: old })]));

    await screen.findByText(/排隊 \d+ 秒，還沒有 worker 接手/);
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "waiting");
    expect(screen.queryByText(/worker 沒有回應/)).toBeNull();
    expect(screen.getByTestId("video-progress-cancel")).toBeTruthy();
  });

  it("a RUNNING stage whose heartbeat stopped past the rule is no worker, by the server's number", async () => {
    const old = new Date(Date.now() - 5 * 60_000).toISOString();
    mount(fileThat([progress({ stage: "rendering", heartbeat_at: old })]));

    await screen.findByText(/worker 沒有回應/);
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "stale");
  });

  it("a file nobody rewrites still crosses the rule: the age is taken at each look, not at the mount", async () => {
    // The runbook's symptom for a missing worker is this sentence appearing
    // after `stale_after_seconds`. The file is byte-identical on every poll
    // — nobody is rewriting it — and the query's structural sharing then
    // hands back the same object, so a component that judged the age at
    // render time never re-rendered and never said it. Each reading now
    // carries WHEN it was read.
    const job = { ...JOB, stale_after_seconds: 1 };
    const heartbeat = new Date(Date.now() - 500).toISOString();
    mount(fileThat([progress({ stage: "rendering", heartbeat_at: heartbeat })]), undefined, job);
    await screen.findByText("錄影中");
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "running");

    await screen.findByText(/worker 沒有回應/, {}, { timeout: 3000 });
    expect(screen.getByTestId("video-progress")).toHaveAttribute("data-state", "stale");
  });

  it("a heartbeat within the rule is an ordinary wait", async () => {
    const fresh = new Date(Date.now() - 20_000).toISOString();
    mount(fileThat([progress({ stage: "queued", elapsed_seconds: 0, heartbeat_at: fresh })]));

    await screen.findByText("影片排隊中");
    expect(screen.queryByText(/worker 沒有回應|還沒有 worker 接手/)).toBeNull();
  });
});

describe("pollDelay — a second at first, backing off to eight", () => {
  it("is 1 s for the first five polls, then doubles to a cap", () => {
    expect([0, 4, 5, 6, 7, 8, 20].map(pollDelay)).toEqual([1000, 1000, 2000, 4000, 8000, 8000, 8000]);
  });
});

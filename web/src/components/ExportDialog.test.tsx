// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatTranscript, ChatVideoQueued } from "../api/chatVideo";
import { renderWithQuery } from "../test/queryWrapper";
import { ExportDialog, type ExportDialogClient } from "./ExportDialog";

/** Ten messages, oldest first, so the newest-first numbering is checkable:
 * #1 in the dialog is `m10`, #10 is `m1`. */
const TRANSCRIPT: ChatTranscript = {
  title: "OOM 事故",
  messages: Array.from({ length: 10 }, (_, i) => ({
    role: i % 2 === 0 ? "user" : "assistant",
    content: `m${i + 1}`,
  })),
};

const QUEUED: ChatVideoQueued = {
  output_path: "/exports/chat-video/OOM-1.mp4",
  source_path: "/exports/chat-video/OOM-1.mp4.chat.json",
  progress_path: "/exports/chat-video/OOM-1.mp4.progress.json",
  expected_seconds: 41,
  stale_after_seconds: 60,
};

function client(over: Partial<ExportDialogClient> = {}): ExportDialogClient {
  return {
    fetchChatTranscript: vi.fn(async () => TRANSCRIPT),
    downloadChatExport: vi.fn(async () => {}),
    startChatVideo: vi.fn(async () => QUEUED),
    fetchChatVideoLimits: vi.fn(async () => ({
      max_pixels: 1920 * 1080,
      max_seconds: 180,
      max_output_bytes: 100_000_000,
    })),
    ...over,
  };
}

function open(c: ExportDialogClient, props: Partial<Parameters<typeof ExportDialog>[0]> = {}) {
  const onClose = vi.fn();
  const onVideoQueued = vi.fn();
  renderWithQuery(
    <ExportDialog
      slug="rca"
      itemId="rca:1"
      chatId="conversation:c1"
      canExportVideo
      onClose={onClose}
      onVideoQueued={onVideoQueued}
      client={c}
      {...props}
    />,
  );
  return { onClose, onVideoQueued };
}

afterEach(() => cleanup());

describe("ExportDialog — text", () => {
  it("downloads the whole thread as JSON by default, once it knows how long the thread is", async () => {
    const c = client();
    const { onClose } = open(c);

    // The count comes from the transcript, not from a guess.
    await screen.findByText("全部（10 則）");
    fireEvent.click(screen.getByTestId("export-submit"));

    await waitFor(() => expect(c.downloadChatExport).toHaveBeenCalledTimes(1));
    expect(c.downloadChatExport).toHaveBeenCalledWith("rca", "rca:1", "conversation:c1", {
      format: "json",
      range: null,
    });
    expect(onClose).toHaveBeenCalled();
    expect(c.startChatVideo).not.toHaveBeenCalled();
  });

  it("markdown of the latest 3 asks the server for [7, 10)", async () => {
    const c = client();
    open(c);
    await screen.findByText("全部（10 則）");

    fireEvent.click(screen.getByTestId("export-kind-md"));
    fireEvent.click(screen.getByTestId("export-range-latest"));
    fireEvent.change(screen.getByTestId("export-latest-n"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("export-submit"));

    await waitFor(() => expect(c.downloadChatExport).toHaveBeenCalledTimes(1));
    expect(c.downloadChatExport).toHaveBeenCalledWith("rca", "rca:1", "conversation:c1", {
      format: "md",
      range: { start: 7, end: 10 },
    });
  });

  it("a custom range is picked from the newest (#1) down, and the picker says which is which", async () => {
    const c = client();
    open(c);
    await screen.findByText("全部（10 則）");

    fireEvent.click(screen.getByTestId("export-range-custom"));
    const from = screen.getByTestId("export-from") as HTMLSelectElement;
    // The newest message is #1 and is labelled as such; the oldest is #10.
    expect(from.options[0].textContent).toContain("#1");
    expect(from.options[0].textContent).toContain("m10");
    expect(from.options[9].textContent).toContain("#10");
    expect(from.options[9].textContent).toContain("m1");
    fireEvent.change(from, { target: { value: "2" } });
    fireEvent.change(screen.getByTestId("export-to"), { target: { value: "4" } });
    fireEvent.click(screen.getByTestId("export-submit"));

    // #2..#4 newest-first = m9, m8, m7 = server indexes 8, 7, 6 → [6, 9)
    await waitFor(() => expect(c.downloadChatExport).toHaveBeenCalledTimes(1));
    expect(c.downloadChatExport).toHaveBeenCalledWith("rca", "rca:1", "conversation:c1", {
      format: "json",
      range: { start: 6, end: 9 },
    });
  });

  it("shows the server's sentence when the export fails, and stays open", async () => {
    const c = client({
      downloadChatExport: vi.fn(async () => {
        throw new Error("匯出失敗：伺服器沒有回傳對話檔");
      }),
    });
    const { onClose } = open(c);
    await screen.findByText("全部（10 則）");

    fireEvent.click(screen.getByTestId("export-submit"));

    await screen.findByText(/匯出失敗：伺服器沒有回傳對話檔/);
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("ExportDialog — video", () => {
  it("posts the sliced transcript, the resolved size and the tempo; hands the queued job back", async () => {
    const c = client();
    const { onClose, onVideoQueued } = open(c);
    await screen.findByText("全部（10 則）");

    fireEvent.click(screen.getByTestId("export-kind-video"));
    await screen.findByTestId("export-size-result");
    fireEvent.click(screen.getByTestId("export-range-latest"));
    fireEvent.change(screen.getByTestId("export-latest-n"), { target: { value: "4" } });
    fireEvent.change(screen.getByTestId("export-resolution"), { target: { value: "1080" } });
    fireEvent.change(screen.getByTestId("export-type-ms"), { target: { value: "40" } });
    // The result line is the same numbers the request will carry.
    expect(screen.getByTestId("export-size-result").textContent).toContain("1920×1080");
    expect(screen.getByTestId("export-size-result").textContent).toContain("1.5×");
    fireEvent.click(screen.getByTestId("export-submit"));

    await waitFor(() => expect(c.startChatVideo).toHaveBeenCalledTimes(1));
    const [slug, itemId, body] = (c.startChatVideo as ReturnType<typeof vi.fn>).mock.calls[0];
    expect([slug, itemId]).toEqual(["rca", "rca:1"]);
    expect(body.transcript.title).toBe("OOM 事故");
    // The latest 4 in server order: m7 m8 m9 m10.
    expect(body.transcript.messages.map((m: { content: string }) => m.content)).toEqual([
      "m7",
      "m8",
      "m9",
      "m10",
    ]);
    expect(body.options).toMatchObject({
      width: 1920,
      height: 1080,
      scale: 0, // automatic: the player's own rule, not a number typed here
      fmt: ["mp4"],
      type_ms: 40,
    });
    expect(body.output_path).toBeNull();
    expect(onVideoQueued).toHaveBeenCalledWith(QUEUED);
    expect(onClose).toHaveBeenCalled();
    expect(c.downloadChatExport).not.toHaveBeenCalled();
  });

  it("text-size mode pins the scale and grows the frame with it", async () => {
    const c = client();
    open(c);
    await screen.findByText("全部（10 則）");
    fireEvent.click(screen.getByTestId("export-kind-video"));
    await screen.findByTestId("export-size-result");

    fireEvent.click(screen.getByTestId("export-size-mode-text"));
    fireEvent.change(screen.getByTestId("export-aspect"), { target: { value: "4:3" } });
    fireEvent.change(screen.getByTestId("export-text-size"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByTestId("export-submit"));

    await waitFor(() => expect(c.startChatVideo).toHaveBeenCalledTimes(1));
    const body = (c.startChatVideo as ReturnType<typeof vi.fn>).mock.calls[0][2];
    expect(body.options).toMatchObject({ width: 1440, height: 1080, scale: 1.5 });
  });

  it("offers only the sizes this deployment allows, and refuses a typed size past them", async () => {
    const c = client({
      fetchChatVideoLimits: vi.fn(async () => ({
        max_pixels: 1280 * 720,
        max_seconds: 60,
        max_output_bytes: 1,
      })),
    });
    open(c);
    await screen.findByText("全部（10 則）");
    fireEvent.click(screen.getByTestId("export-kind-video"));
    await screen.findByTestId("export-size-result");

    // 1080p is 2,073,600 pixels: past a 720p ceiling, so not on the list.
    const steps = Array.from(
      (screen.getByTestId("export-resolution") as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(steps).toEqual(["480", "720"]);
    // The max-seconds field is capped where the deployment caps it.
    expect((screen.getByTestId("export-max-seconds") as HTMLInputElement).max).toBe("60");

    fireEvent.click(screen.getByTestId("export-size-mode-custom"));
    fireEvent.change(screen.getByTestId("export-width"), { target: { value: "1920" } });
    fireEvent.change(screen.getByTestId("export-height"), { target: { value: "1080" } });

    expect(screen.getByTestId("export-size-error").textContent).toContain("1280×720");
    expect(screen.getByTestId("export-submit")).toBeDisabled();

    // Back inside the ceiling, the request carries the deployment's max
    // seconds, not the form's 90-second default the ceiling is below.
    fireEvent.click(screen.getByTestId("export-size-mode-resolution"));
    fireEvent.click(screen.getByTestId("export-submit"));
    await waitFor(() => expect(c.startChatVideo).toHaveBeenCalledTimes(1));
    const body = (c.startChatVideo as ReturnType<typeof vi.fn>).mock.calls[0][2];
    expect(body.options.max_seconds).toBe(60);
    expect(body.options.width * body.options.height).toBeLessThanOrEqual(1280 * 720);
  });

  it("is offered but locked without the two verbs the route asks", async () => {
    const c = client();
    open(c, { canExportVideo: false });
    await screen.findByText("全部（10 則）");

    expect(screen.getByTestId("export-kind-video")).toBeDisabled();
    expect(screen.getByText(/需要這個項目的「讀取檔案」與「新增檔案」權限/)).toBeTruthy();
    expect(c.fetchChatVideoLimits).not.toHaveBeenCalled();
  });

  it("shows the route's refusal and stays open", async () => {
    const c = client({
      startChatVideo: vi.fn(async () => {
        throw new Error("a video is already being made at /exports/chat-video/OOM-1.mp4");
      }),
    });
    const { onClose } = open(c);
    await screen.findByText("全部（10 則）");
    fireEvent.click(screen.getByTestId("export-kind-video"));
    await screen.findByTestId("export-size-result");

    fireEvent.click(screen.getByTestId("export-submit"));

    await screen.findByText(/a video is already being made at/);
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("ExportDialog — leaving (#779)", () => {
  it("asks before dropping a changed form, and keeps it", async () => {
    const c = client();
    const { onClose } = open(c);
    await screen.findByText("全部（10 則）");
    fireEvent.click(screen.getByTestId("export-kind-md"));

    fireEvent.click(screen.getByTestId("export-cancel"));

    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("dialog-action-keep"));
    expect(screen.getByTestId("export-kind-md")).toBeChecked();
  });

  it("closes without asking when nothing was changed", async () => {
    const c = client();
    const { onClose } = open(c);
    await screen.findByText("全部（10 則）");

    fireEvent.click(screen.getByTestId("export-cancel"));

    expect(onClose).toHaveBeenCalled();
  });
});

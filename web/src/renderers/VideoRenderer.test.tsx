// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FileServiceProvider, investigationFileService } from "../api/fileService";
import { EditModeProvider } from "../hooks/editMode";
import { pickRenderer } from "./registry";
import { VideoRenderer } from "./VideoRenderer";

afterEach(cleanup);

describe("VideoRenderer — a chat video opened from the tree plays, it is not dumped as bytes", () => {
  // Seen in the P9 demo: "開啟" on the finished mp4 landed in the text editor
  // with 2.4 MB of mojibake and an "invisible unicode characters" banner.
  it("is what the registry picks for mp4 and webm", () => {
    expect(pickRenderer("/exports/chat-video/OOM-1.mp4")).toBe("video");
    expect(pickRenderer("/videos/x.WEBM")).toBe("video");
    // gif stays a picture: the browser animates it as an <img>.
    expect(pickRenderer("/exports/chat-video/OOM-1.gif")).toBe("image");
  });

  it("streams the file from the item's file route in a <video> with controls", () => {
    render(
      <EditModeProvider>
        <FileServiceProvider value={investigationFileService("rca", "rca:1")}>
          <VideoRenderer path="/exports/chat-video/OOM 事故-1.mp4" />
        </FileServiceProvider>
      </EditModeProvider>,
    );

    const video = screen.getByTestId("video-renderer") as HTMLVideoElement;
    expect(video.tagName).toBe("VIDEO");
    expect(video.hasAttribute("controls")).toBe(true);
    // The route the browser fetches — not a Blob of the whole file through the
    // editor buffer (a 100 MB video is not something to hold in a string).
    expect(video.getAttribute("src")).toContain("/a/rca/items/rca%3A1/files/");
    expect(video.getAttribute("src")).toContain("OOM");
  });
});

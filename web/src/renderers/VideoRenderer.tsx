/**
 * Video preview (mp4 / webm) — the browser's own player over the item's file
 * route. Without this a chat video opened from the tree fell through to the
 * catch-all text editor and showed 2.4 MB of mojibake under an "invisible
 * unicode characters" banner (seen in the plan-chat-video-export demo).
 *
 * Unlike the image / pdf renderers this does NOT go through the editor
 * buffer: those build a Blob URL from the buffer's bytes so an edit shows at
 * once, but a video is never edited here and may be up to
 * `chat_video.max_output_bytes` (100 MB) — bytes the browser should stream
 * from the route, not hold in a string. The Edit toggle still flips to the
 * byte editor like every other file (#all-editable).
 */
import { useFileService } from "../api/fileService";
import { useEditMode } from "../hooks/editMode";
import { relPath } from "../lib/relPath";
import { TextRenderer } from "./TextRenderer";

export function VideoRenderer({ path }: { path: string }) {
  const { isEditing } = useEditMode();
  const svc = useFileService();
  if (isEditing(path)) return <TextRenderer path={path} />;
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#000",
      }}
    >
      {/* No caption track: the transcript IS the video's words. */}
      <video
        data-testid="video-renderer"
        controls
        src={svc.fileDownloadUrl(path)}
        title={relPath(path)}
        style={{ maxWidth: "100%", maxHeight: "100%" }}
      />
    </div>
  );
}

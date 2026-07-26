/**
 * The files an agent put in front of the user, rendered in the chat: an image as
 * the image, any other file as a card with one way to open it.
 *
 * Not inside the collapsed tool card ordinary results use — a chart behind a
 * `<details>` is the same failure as a path in prose.
 */
import { useOpenFile, useWorkspaceVisible } from "../hooks/openFile";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { isInlineImage, type ShownFile } from "../renderers/shownFiles";
import { Icon } from "./Icon";

/** Thumbnail edge, px. Recognisable at a glance without taking over the thread. */
const THUMB = 260;

export function ShownFiles({
  files,
  fileUrl,
}: {
  files: ShownFile[];
  /** Resolves a workspace path to a content URL. Absent on surfaces with no item
   * scope, which then show the filename without a way to fetch it. */
  fileUrl?: (path: string) => string;
}) {
  // Only offer "open in the workspace" when the workspace is actually on screen;
  // folded away, `openFile` would open a tab the user cannot see.
  const opener = useOpenFile();
  const openFile = useWorkspaceVisible() ? opener : null;
  if (files.length === 0) return null;
  return (
    <div
      data-testid="shown-files"
      style={{
        display: "flex",
        flexDirection: "column",
        // Hug the content: a column flex parent stretches its children by
        // default, which blew the card out to the full pane width around a
        // 260px thumbnail.
        alignItems: "flex-start",
        gap: 8,
        marginLeft: 28,
        marginTop: 4,
      }}
    >
      {files.map((file) => (
        <ShownFileView key={file.path} file={file} fileUrl={fileUrl} openFile={openFile} />
      ))}
    </div>
  );
}

function ShownFileView({
  file,
  fileUrl,
  openFile,
}: {
  file: ShownFile;
  fileUrl?: (path: string) => string;
  openFile: ((path: string, opts?: { preview?: boolean }) => void) | null;
}) {
  const t = useT();
  const name = basename(file.path);
  const url = fileUrl?.(file.path);
  const inline = isInlineImage(file) && url;

  const body = (
    <>
      {inline && (
        // A THUMBNAIL, not the picture at full size: the chat is a conversation,
        // and a chart that fills the pane pushes the words that explain it off
        // screen. Big enough to recognise, click to actually look.
        <img
          src={url}
          alt={file.caption ?? name}
          style={{
            display: "block",
            maxWidth: THUMB,
            maxHeight: THUMB,
            borderRadius: "var(--radius-card)",
            border: "1px solid var(--paper-3)",
          }}
        />
      )}
      {file.caption && (
        <div style={{ fontSize: pxToRem(13), color: "var(--text-paper)" }}>{file.caption}</div>
      )}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-mono)",
          fontSize: pxToRem(11),
          color: "var(--text-paper-d)",
        }}
      >
        {!inline && <Icon name="file" size={12} color="var(--text-paper-d)" />}
        <span>{name}</span>
        <span>·</span>
        <span>{formatBytes(file.size)}</span>
        {(openFile || url) && (
          <>
            <span>·</span>
            <span>{t(openFile ? "shownFile.openHere" : "shownFile.open")}</span>
          </>
        )}
      </div>
    </>
  );

  // The whole thing is one target, so clicking the image and clicking its name do
  // the same thing. Opening in the workspace wins when there is a shell to open
  // in (renderers/registry.ts already renders every one of these types); a plain
  // link is the fallback, and with neither we render no control at all rather
  // than one that leads nowhere.
  if (openFile) {
    return (
      <button type="button" onClick={() => openFile(file.path)} style={frame({ button: true })}>
        {body}
      </button>
    );
  }
  if (url) {
    return (
      <a href={url} target="_blank" rel="noreferrer" style={frame({})}>
        {body}
      </a>
    );
  }
  return <div style={frame({})}>{body}</div>;
}

function frame({ button }: { button?: boolean }): React.CSSProperties {
  return {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 4,
    padding: 8,
    background: "var(--white)",
    border: "1px solid var(--paper-3)",
    borderRadius: "var(--radius-card)",
    textAlign: "left",
    color: "inherit",
    textDecoration: "none",
    maxWidth: "100%",
    ...(button ? { cursor: "pointer", font: "inherit" } : {}),
  };
}

/** The filename, for surfaces that show a name rather than a path. */
function basename(path: string): string {
  return path.split("/").pop() || path;
}

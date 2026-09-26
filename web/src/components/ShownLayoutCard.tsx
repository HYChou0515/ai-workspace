/**
 * A `show_file(layout=…)` arrangement in the chat (#847 PR 3 P4): ONE card for
 * the whole thing, drawn as a miniature of its panes, that opens it in one
 * click — in the workspace's split panes when the workspace is on screen, else
 * on the item's editor-area page in a new tab (chat mode, `useViewPageHref`).
 */
import type { ReactNode } from "react";

import { useOptionalFileService } from "../api/fileService";
import { useOpenLayout, useViewPageHref, useWorkspaceVisible } from "../hooks/openFile";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import type { LayoutNode } from "../pages/investigation/paneTree";
import type { ShownLayout } from "../renderers/shownFiles";
import { Icon } from "./Icon";
import { isViewFile, useSeenOnce, useViewThumbnail } from "./ViewThumbnail";

/** Miniature size, px: wide enough for three filenames side by side, and for
 * each pane's thumbnail (P6) to read as its chart. Narrower on a phone: the
 * box gives way to the card (`maxWidth`). */
const MINI_W = 360;
const MINI_H = 200;

export function ShownLayoutCard({ shown }: { shown: ShownLayout }) {
  const t = useT();
  const opener = useOpenLayout();
  const openLayout = useWorkspaceVisible() ? opener : null;
  const href = useViewPageHref()?.({ layout: shown.layout });
  const count = shown.files.length;
  // One watch for the whole card (#847/#848 P6): its panes draw their
  // thumbnails together, the first time the card is on screen. With no item
  // workspace to read from, every pane stays its filename.
  const [ref, seen] = useSeenOnce<HTMLDivElement>();
  const thumbs = useOptionalFileService() !== null;

  const body = (
    <>
      {shown.caption && (
        <div style={{ fontSize: pxToRem(13), color: "var(--text-paper)" }}>{shown.caption}</div>
      )}
      <div style={{ width: MINI_W, maxWidth: "100%", height: MINI_H, display: "flex" }} aria-hidden>
        <Mini node={shown.layout} seen={seen} thumbs={thumbs} />
      </div>
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
        <Icon name="split" size={12} color="var(--text-paper-d)" />
        <span>{t("shownLayout.files", { n: count })}</span>
        {(openLayout || href) && (
          <>
            <span>·</span>
            <span>{t(openLayout ? "shownFile.openHere" : "shownFile.open")}</span>
          </>
        )}
      </div>
    </>
  );

  const wrap = { marginLeft: 28, marginTop: 4 } as const;
  if (openLayout) {
    return (
      <div ref={ref} data-testid="shown-layout" style={wrap}>
        <button type="button" onClick={() => openLayout(shown.layout)} style={frame(true)}>
          {body}
        </button>
      </div>
    );
  }
  if (href) {
    return (
      <div ref={ref} data-testid="shown-layout" style={wrap}>
        <a href={href} target="_blank" rel="noreferrer" style={frame(false)}>
          {body}
        </a>
      </div>
    );
  }
  return (
    <div ref={ref} data-testid="shown-layout" style={wrap}>
      <div style={frame(false)}>{body}</div>
    </div>
  );
}

type MiniProps = { node: LayoutNode; seen: boolean; thumbs: boolean };

function Mini({ node, seen, thumbs }: MiniProps) {
  if (node.type === "leaf") {
    if (thumbs && isViewFile(node.path)) return <ViewPane path={node.path} seen={seen} />;
    return <Pane path={node.path} thumb={null} />;
  }
  return (
    <div
      data-testid="shown-layout-split"
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: node.dir === "row" ? "row" : "column",
        gap: 3,
      }}
    >
      <div style={{ flexGrow: node.ratio, flexBasis: 0, display: "flex", minWidth: 0, minHeight: 0 }}>
        <Mini node={node.a} seen={seen} thumbs={thumbs} />
      </div>
      <div
        style={{ flexGrow: 1 - node.ratio, flexBasis: 0, display: "flex", minWidth: 0, minHeight: 0 }}
      >
        <Mini node={node.b} seen={seen} thumbs={thumbs} />
      </div>
    </div>
  );
}

function ViewPane({ path, seen }: { path: string; seen: boolean }) {
  return <Pane path={path} thumb={useViewThumbnail(path, seen)} />;
}

/** One pane: its view's thumbnail when there is one, else its filename. */
function Pane({ path, thumb }: { path: string; thumb: ReactNode | null }) {
  return (
    <div
      data-testid="shown-layout-pane"
      title={path}
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: thumb ? 0 : 4,
        background: "var(--paper-2)",
        border: "1px solid var(--paper-3)",
        borderRadius: 4,
        fontFamily: "var(--font-mono)",
        fontSize: pxToRem(10),
        color: "var(--text-paper)",
        overflow: "hidden",
        whiteSpace: "nowrap",
        textOverflow: "ellipsis",
      }}
    >
      {thumb ?? (
        // A block, not the flex item: `text-overflow` is ignored on a flex
        // container's own text.
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{basename(path)}</span>
      )}
    </div>
  );
}

function frame(button: boolean): React.CSSProperties {
  return {
    // Hugs the miniature, as a file card hugs its thumbnail, instead of
    // stretching a mostly empty frame across the whole thread.
    display: "inline-flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 6,
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

function basename(path: string): string {
  return path.split("/").pop() || path;
}

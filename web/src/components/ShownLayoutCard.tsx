/**
 * A `show_file(layout=…)` arrangement in the chat (#847 PR 3 P4): ONE card for
 * the whole thing, drawn as a miniature of its panes, that opens it in one
 * click — in the workspace's split panes when the workspace is on screen, else
 * on the editor-area page (`href`, chat mode).
 */
import { useOpenLayout, useWorkspaceVisible } from "../hooks/openFile";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import type { LayoutNode } from "../pages/investigation/paneTree";
import type { ShownLayout } from "../renderers/shownFiles";
import { Icon } from "./Icon";

/** Miniature size, px: wide enough for three filenames side by side. */
const MINI_W = 280;
const MINI_H = 140;

export function ShownLayoutCard({ shown, href }: { shown: ShownLayout; href?: string }) {
  const t = useT();
  const opener = useOpenLayout();
  const openLayout = useWorkspaceVisible() ? opener : null;
  const count = shown.files.length;

  const body = (
    <>
      {shown.caption && (
        <div style={{ fontSize: pxToRem(13), color: "var(--text-paper)" }}>{shown.caption}</div>
      )}
      <div style={{ width: MINI_W, height: MINI_H, display: "flex" }} aria-hidden>
        <Mini node={shown.layout} />
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
      <div data-testid="shown-layout" style={wrap}>
        <button type="button" onClick={() => openLayout(shown.layout)} style={frame(true)}>
          {body}
        </button>
      </div>
    );
  }
  if (href) {
    return (
      <div data-testid="shown-layout" style={wrap}>
        <a href={href} target="_blank" rel="noreferrer" style={frame(false)}>
          {body}
        </a>
      </div>
    );
  }
  return (
    <div data-testid="shown-layout" style={wrap}>
      <div style={frame(false)}>{body}</div>
    </div>
  );
}

function Mini({ node }: { node: LayoutNode }) {
  if (node.type === "leaf") {
    return (
      <div
        data-testid="shown-layout-pane"
        title={node.path}
        style={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 4,
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
        {/* A block, not the flex item: `text-overflow` is ignored on a flex
            container's own text. */}
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{basename(node.path)}</span>
      </div>
    );
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
        <Mini node={node.a} />
      </div>
      <div
        style={{ flexGrow: 1 - node.ratio, flexBasis: 0, display: "flex", minWidth: 0, minHeight: 0 }}
      >
        <Mini node={node.b} />
      </div>
    </div>
  );
}

function frame(button: boolean): React.CSSProperties {
  return {
    display: "flex",
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

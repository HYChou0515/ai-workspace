/**
 * F12 — Fishbone (6M) renderer. Read-only SVG of a .canvas file written
 * by the agent. Schema agnostic to the BE — see contract.md §5.
 *
 * Layout: horizontal spine, 6 branches with diagonal stems alternating
 * top/bot, branch labels at the tip, items along the stem. `strong: true`
 * items render in accent (orange) bold.
 */

import { useFileBuffer } from "../../hooks/fileBuffer";
import { type FishboneBranch, parseFishbone } from "./schema";

const WIDTH = 880;
const HEIGHT = 480;
const PAD_X = 80;

export function FishboneRenderer({ path }: { investigationId: string; path: string }) {
  const { entry } = useFileBuffer(path);
  if (entry.status === "loading") return <Status>Loading {path}…</Status>;
  if (entry.status === "error") {
    return <Status tone="err">{entry.error ?? "load failed"}</Status>;
  }
  if (entry.kind !== "text") {
    return <Status>Binary .canvas file — cannot render.</Status>;
  }
  const fb = parseFishbone(entry.text);
  if (!fb) {
    return (
      <div>
        <Status tone="err">Schema mismatch — showing raw JSON:</Status>
        <pre
          style={{
            marginTop: 8,
            padding: 12,
            background: "var(--paper-2)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            overflow: "auto",
          }}
        >
          {entry.text}
        </pre>
      </div>
    );
  }

  const top = fb.branches.filter((b) => b.side === "top");
  const bot = fb.branches.filter((b) => b.side === "bot");
  const spineY = HEIGHT / 2;
  const slotsTop = Math.max(top.length, 1);
  const slotsBot = Math.max(bot.length, 1);
  const span = WIDTH - PAD_X * 2;
  const stepTop = span / (slotsTop + 1);
  const stepBot = span / (slotsBot + 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="caps">{path}</div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        style={{ width: "100%", maxWidth: 1000, height: "auto" }}
      >
        {/* Spine */}
        <line
          x1={PAD_X / 2}
          y1={spineY}
          x2={WIDTH - PAD_X}
          y2={spineY}
          stroke="var(--ink-2)"
          strokeWidth="2"
        />
        {/* Effect box */}
        <rect
          x={WIDTH - PAD_X}
          y={spineY - 28}
          width={PAD_X}
          height={56}
          rx={8}
          fill="var(--ink)"
        />
        <foreignObject
          x={WIDTH - PAD_X}
          y={spineY - 28}
          width={PAD_X}
          height={56}
        >
          <div
            style={{
              width: "100%",
              height: "100%",
              color: "var(--text-dark)",
              padding: "0 6px",
              fontSize: 11,
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              lineHeight: 1.25,
            }}
          >
            {fb.effect}
          </div>
        </foreignObject>

        {top.map((b, i) => (
          <Branch
            key={`t-${b.label}`}
            branch={b}
            anchorX={PAD_X + stepTop * (i + 1)}
            anchorY={spineY}
            side="top"
          />
        ))}
        {bot.map((b, i) => (
          <Branch
            key={`b-${b.label}`}
            branch={b}
            anchorX={PAD_X + stepBot * (i + 1)}
            anchorY={spineY}
            side="bot"
          />
        ))}
      </svg>
    </div>
  );
}

function Branch({
  branch,
  anchorX,
  anchorY,
  side,
}: {
  branch: FishboneBranch;
  anchorX: number;
  anchorY: number;
  side: "top" | "bot";
}) {
  const len = 130;
  const dy = side === "top" ? -1 : 1;
  // Diagonal stem from spine up/down and to the left.
  const tipX = anchorX - 90;
  const tipY = anchorY + dy * len;

  return (
    <g>
      <line
        x1={anchorX}
        y1={anchorY}
        x2={tipX}
        y2={tipY}
        stroke="var(--ink-2)"
        strokeWidth={1.5}
      />
      <text
        x={tipX - 6}
        y={tipY + (side === "top" ? -6 : 14)}
        textAnchor="end"
        fontFamily="var(--font-display)"
        fontSize="13"
        fontWeight="700"
        fill="var(--ink-2)"
      >
        {branch.label}
      </text>
      {branch.items.map((it, i) => {
        const fraction = (i + 1) / (branch.items.length + 1);
        const x = anchorX - 90 * fraction;
        const y = anchorY + dy * len * fraction;
        const fill = it.strong ? "var(--accent)" : "var(--text-paper)";
        const weight = it.strong ? 700 : 500;
        return (
          <g key={i}>
            <line
              x1={x}
              y1={y}
              x2={x + 20}
              y2={y}
              stroke={it.strong ? "var(--accent)" : "var(--paper-3)"}
              strokeWidth={it.strong ? 1.5 : 1}
            />
            <text
              x={x + 22}
              y={y + 3}
              fontSize="11"
              fill={fill}
              fontWeight={weight}
              fontFamily="var(--font-body)"
            >
              {it.t}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function Status({
  children,
  tone = "muted",
}: {
  children: React.ReactNode;
  tone?: "muted" | "err";
}) {
  return (
    <div
      style={{
        color: tone === "err" ? "var(--err)" : "var(--text-paper-d)",
        fontSize: "var(--text-body)",
      }}
    >
      {children}
    </div>
  );
}

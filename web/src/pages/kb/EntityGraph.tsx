/**
 * #534 — the entity AS A GRAPH: this identity is the centre node, the corpus
 * around it. Documents that name it hang on the left (dashed evidence edges,
 * labelled with the verbatim surface); related entities hang on the right
 * (solid edges labelled with the predicate, click to re-centre the graph on
 * that entity). HTML nodes over an SVG edge underlay — real links, real hover,
 * no graph library.
 */
import { Link } from "react-router-dom";

import { useT } from "../../lib/i18n";
import { docLabel } from "./CardAttachments";
import { docHref } from "./kbLinks";

export type GraphDoc = { source_doc_id: string; surface: string; occurrences: number };
export type GraphRel = {
  direction: string;
  predicate: string;
  other_name: string;
  other_entity_id: string;
};

type Node = { x: number; y: number };

/** Spread `n` points over an arc (degrees), centred vertically. */
function arc(n: number, from: number, to: number, radius: number): Node[] {
  const out: Node[] = [];
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0.5 : i / (n - 1);
    const a = ((from + (to - from) * t) * Math.PI) / 180;
    out.push({ x: 50 + radius * Math.cos(a), y: 50 + radius * Math.sin(a) });
  }
  return out;
}

export function EntityGraph({
  name,
  kind,
  docs,
  rels,
}: {
  name: string;
  kind: string;
  docs: GraphDoc[];
  rels: GraphRel[];
}) {
  const t = useT();
  // Documents on the left arc, relations on the right — the page reads
  // "evidence ← the thing → what it connects to".
  const docPos = arc(docs.length, 145, 215, 38);
  const relPos = arc(rels.length, -35, 35, 38);

  const edge = (p: Node, dashed: boolean) => (
    <line
      x1="50"
      y1="50"
      x2={p.x}
      y2={p.y}
      stroke="var(--paper-3)"
      strokeWidth={1.5}
      strokeDasharray={dashed ? "4 4" : undefined}
      vectorEffect="non-scaling-stroke"
    />
  );
  const mid = (p: Node): Node => ({ x: (p.x + 50) / 2, y: (p.y + 50) / 2 });

  return (
    <div className="ent-graph" data-testid="entity-graph" aria-label={t("entity.graphAria")}>
      <svg
        className="ent-graph__edges"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden
      >
        {docs.map((d, i) => (
          <g key={`d:${d.source_doc_id}:${d.surface}`}>{edge(docPos[i], true)}</g>
        ))}
        {rels.map((r, i) => (
          <g key={`r:${r.predicate}:${r.other_name}:${i}`}>{edge(relPos[i], false)}</g>
        ))}
      </svg>

      {/* edge labels — the words ON the connections */}
      {docs.map((d, i) => {
        const m = mid(docPos[i]);
        return (
          <span
            key={`dl:${d.source_doc_id}:${d.surface}`}
            className="ent-graph__edge-label"
            style={{ left: `${m.x}%`, top: `${m.y}%` }}
          >
            「{d.surface}」{d.occurrences > 1 ? `×${d.occurrences}` : ""}
          </span>
        );
      })}
      {rels.map((r, i) => {
        const m = mid(relPos[i]);
        return (
          <span
            key={`rl:${r.predicate}:${r.other_name}:${i}`}
            className="ent-graph__edge-label ent-graph__edge-label--rel"
            style={{ left: `${m.x}%`, top: `${m.y}%` }}
          >
            {r.direction === "in" ? `←${r.predicate}` : `${r.predicate}→`}
          </span>
        );
      })}

      {/* the centre — this entity */}
      <div className="ent-graph__center" style={{ left: "50%", top: "50%" }}>
        <span className="ent-graph__center-name">{name}</span>
        {kind ? <span className="ent-graph__center-kind">{kind}</span> : null}
      </div>

      {/* documents — evidence nodes */}
      {docs.map((d, i) => (
        <a
          key={`dn:${d.source_doc_id}:${d.surface}`}
          className="ent-graph__node ent-graph__node--doc"
          style={{ left: `${docPos[i].x}%`, top: `${docPos[i].y}%` }}
          href={docHref(d.source_doc_id, d.surface)}
          target="_blank"
          rel="noreferrer"
          title={d.source_doc_id}
        >
          {docLabel(d.source_doc_id)}
        </a>
      ))}

      {/* related entities — click to re-centre */}
      {rels.map((r, i) =>
        r.other_entity_id ? (
          <Link
            key={`rn:${r.predicate}:${r.other_name}:${i}`}
            className="ent-graph__node ent-graph__node--entity"
            style={{ left: `${relPos[i].x}%`, top: `${relPos[i].y}%` }}
            to={`/kb/graph/entities/${r.other_entity_id}`}
          >
            {r.other_name}
          </Link>
        ) : (
          <span
            key={`rn:${r.predicate}:${r.other_name}:${i}`}
            className="ent-graph__node ent-graph__node--stray"
            style={{ left: `${relPos[i].x}%`, top: `${relPos[i].y}%` }}
            title={t("entity.strayTitle")}
          >
            {r.other_name}
          </span>
        ),
      )}
    </div>
  );
}

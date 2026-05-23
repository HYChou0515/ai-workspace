/**
 * Subset of nbformat v4 we actually render. Tolerant of missing optional
 * fields — agent-written notebooks may skip them.
 */

export type NbOutput =
  | { output_type: "stream"; name: "stdout" | "stderr"; text: string | string[] }
  | { output_type: "display_data"; data: Record<string, string | string[]>; metadata?: Record<string, unknown> }
  | { output_type: "execute_result"; data: Record<string, string | string[]>; execution_count?: number | null; metadata?: Record<string, unknown> }
  | { output_type: "error"; ename: string; evalue: string; traceback: string[] };

export type NbCell =
  | {
      cell_type: "code";
      source: string | string[];
      outputs?: NbOutput[];
      execution_count?: number | null;
      metadata?: Record<string, unknown>;
    }
  | {
      cell_type: "markdown";
      source: string | string[];
      metadata?: Record<string, unknown>;
    }
  | {
      cell_type: "raw";
      source: string | string[];
      metadata?: Record<string, unknown>;
    };

export type Notebook = {
  cells: NbCell[];
  metadata?: Record<string, unknown>;
  nbformat?: number;
  nbformat_minor?: number;
};

/** Cells expose `source` as either a string or string[]; flatten. */
export function cellSource(cell: NbCell): string {
  return Array.isArray(cell.source) ? cell.source.join("") : cell.source;
}

/**
 * Parse a notebook JSON string with defensive fallbacks. Throws only on
 * truly unparseable JSON; missing/empty fields normalise to safe defaults.
 */
export function parseNotebook(text: string): Notebook {
  const raw = JSON.parse(text) as unknown;
  if (!raw || typeof raw !== "object") throw new Error("invalid notebook root");
  const obj = raw as Record<string, unknown>;
  const cells = Array.isArray(obj.cells) ? (obj.cells as NbCell[]) : [];
  return {
    cells,
    metadata: typeof obj.metadata === "object" ? (obj.metadata as Record<string, unknown>) : {},
    nbformat: typeof obj.nbformat === "number" ? obj.nbformat : 4,
    nbformat_minor: typeof obj.nbformat_minor === "number" ? obj.nbformat_minor : 5,
  };
}

export function emptyNotebook(): Notebook {
  return { cells: [], metadata: {}, nbformat: 4, nbformat_minor: 5 };
}

/** Top mime type per F9 priority. */
export function pickMime(data: Record<string, string | string[]>): {
  mime: string;
  body: string;
} | null {
  const order = ["image/png", "text/html", "text/plain"];
  for (const m of order) {
    const v = data[m];
    if (v != null) {
      const body = Array.isArray(v) ? v.join("") : v;
      return { mime: m, body };
    }
  }
  // Fallback to first non-priority mime.
  const keys = Object.keys(data);
  if (keys.length === 0) return null;
  const k = keys[0];
  if (!k) return null;
  const v = data[k];
  if (v == null) return null;
  return { mime: k, body: Array.isArray(v) ? v.join("") : v };
}

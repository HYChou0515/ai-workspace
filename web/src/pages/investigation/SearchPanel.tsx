/**
 * VSCode-style global search + replace panel (#8). Searches file contents
 * across the investigation's FileStore with regex / whole-word / match-case
 * toggles and include/exclude globs, groups hits by file, and can replace
 * every match in one shot.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../api";
import type { SearchOptions, SearchResult } from "../../api/types";
import { Icon } from "../../components/Icon";
import { basename, dirname } from "./renderer";

type SearchClient = {
  searchFiles: (id: string, query: string, opts?: SearchOptions) => Promise<SearchResult[]>;
  replaceInFiles: (
    id: string,
    query: string,
    replacement: string,
    opts?: SearchOptions,
  ) => Promise<number>;
};

type OpenFileFn = (path: string, opts?: { preview?: boolean }) => void;

const DEBOUNCE_MS = 200;

export function SearchPanel({
  investigationId,
  onOpenFile,
  client = api,
}: {
  investigationId: string;
  onOpenFile: OpenFileFn;
  client?: SearchClient;
}) {
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [showReplace, setShowReplace] = useState(false);

  const [results, setResults] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const opts = useMemo<SearchOptions>(
    () => ({ regex, caseSensitive, wholeWord, include, exclude }),
    [regex, caseSensitive, wholeWord, include, exclude],
  );

  const runSearch = useCallback(async () => {
    if (!query) {
      setResults([]);
      setError(null);
      return;
    }
    setBusy(true);
    try {
      const res = await client.searchFiles(investigationId, query, opts);
      setResults(res);
      setError(null);
    } catch (e) {
      setResults([]);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [client, investigationId, query, opts]);

  // Search-as-you-type, debounced.
  useEffect(() => {
    const t = setTimeout(runSearch, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [runSearch]);

  const replaceAll = useCallback(async () => {
    if (!query) return;
    setBusy(true);
    try {
      await client.replaceInFiles(investigationId, query, replacement, opts);
      await runSearch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [client, investigationId, query, replacement, opts, runSearch]);

  const totalMatches = results.reduce((n, r) => n + r.matches.length, 0);

  // A display-only regex for highlighting the matched span inside each line.
  const hlRe = useMemo(() => safeHighlightRe(query, opts), [query, opts]);

  const toggleFile = (path: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <aside style={frame}>
      <div style={header}>
        <span className="caps">Search</span>
        {busy && <span style={{ fontSize: 11, color: "var(--text-paper-d2)" }}>searching…</span>}
      </div>

      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "stretch", gap: 4 }}>
          <button
            type="button"
            aria-label={showReplace ? "Hide replace" : "Show replace"}
            onClick={() => setShowReplace((v) => !v)}
            style={{
              ...iconToggle,
              width: 20,
              alignSelf: "stretch",
              color: "var(--text-paper-d)",
            }}
          >
            <Icon name={showReplace ? "chev_d" : "chev_r"} size={12} />
          </button>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={fieldWrap}>
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search"
                style={input}
              />
              <div style={{ display: "flex", gap: 2 }}>
                <Toggle label="Match case" on={caseSensitive} onClick={() => setCaseSensitive((v) => !v)}>
                  Aa
                </Toggle>
                <Toggle label="Whole word" on={wholeWord} onClick={() => setWholeWord((v) => !v)}>
                  <span style={{ textDecoration: "underline" }}>ab</span>
                </Toggle>
                <Toggle label="Use regex" on={regex} onClick={() => setRegex((v) => !v)}>
                  .*
                </Toggle>
              </div>
            </div>

            {showReplace && (
              <div style={fieldWrap}>
                <input
                  value={replacement}
                  onChange={(e) => setReplacement(e.target.value)}
                  placeholder="Replace"
                  style={input}
                />
                <button
                  type="button"
                  onClick={() => void replaceAll()}
                  disabled={!query || busy}
                  title="Replace all"
                  aria-label="Replace all"
                  style={{ ...iconToggle, width: 26, opacity: !query || busy ? 0.5 : 1 }}
                >
                  <Icon name="check" size={13} />
                </button>
              </div>
            )}
          </div>
        </div>

        <input
          value={include}
          onChange={(e) => setInclude(e.target.value)}
          placeholder="files to include (e.g. *.md, src/**)"
          style={{ ...input, fontSize: 11 }}
        />
        <input
          value={exclude}
          onChange={(e) => setExclude(e.target.value)}
          placeholder="files to exclude"
          style={{ ...input, fontSize: 11 }}
        />
      </div>

      <div style={{ padding: "0 12px 6px", fontSize: 11, color: "var(--text-paper-d)" }}>
        {error ? (
          <span style={{ color: "var(--danger, #b4413c)" }}>{error}</span>
        ) : query && results.length > 0 ? (
          `${totalMatches} result${totalMatches === 1 ? "" : "s"} in ${results.length} file${
            results.length === 1 ? "" : "s"
          }`
        ) : query && !busy ? (
          "No results."
        ) : null}
      </div>

      <div className="scrollable" style={{ flex: 1, overflowY: "auto" }}>
        {results.map((r) => {
          const isCollapsed = collapsed.has(r.path);
          return (
            <div key={r.path}>
              <button
                type="button"
                onClick={() => toggleFile(r.path)}
                style={fileHeader}
                title={r.path}
              >
                <Icon name={isCollapsed ? "chev_r" : "chev_d"} size={12} />
                <span style={{ fontWeight: 600 }}>{basename(r.path)}</span>
                <span style={{ color: "var(--text-paper-d2)", fontSize: 11 }}>
                  {dirname(r.path)}
                </span>
                <span style={{ flex: 1 }} />
                <span style={countBadge}>{r.matches.length}</span>
              </button>
              {!isCollapsed &&
                r.matches.map((m, i) => (
                  <button
                    key={`${m.line}:${m.col}:${i}`}
                    type="button"
                    onClick={() => onOpenFile(r.path, { preview: true })}
                    style={matchRow}
                    title={`${r.path}:${m.line}:${m.col}`}
                  >
                    <span style={lineNo}>{m.line}</span>
                    <span style={lineText}>{highlight(m.text, hlRe)}</span>
                  </button>
                ))}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function Toggle({
  label,
  on,
  onClick,
  children,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={on}
      title={label}
      onClick={onClick}
      style={{
        ...iconToggle,
        width: 22,
        fontSize: 11,
        fontWeight: 600,
        background: on ? "var(--accent-soft)" : "transparent",
        color: on ? "var(--accent-h)" : "var(--text-paper-d)",
        border: on ? "1px solid var(--accent)" : "1px solid transparent",
      }}
    >
      {children}
    </button>
  );
}

/** Build a global regex for highlighting; never throws (invalid regex →
 * null, so we just render the plain line). */
function safeHighlightRe(query: string, opts: SearchOptions): RegExp | null {
  if (!query) return null;
  try {
    let pattern = opts.regex ? query : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (opts.wholeWord) pattern = `\\b(?:${pattern})\\b`;
    return new RegExp(pattern, opts.caseSensitive ? "g" : "gi");
  } catch {
    return null;
  }
}

/** Split a line into plain + <mark> segments around regex matches. */
function highlight(text: string, re: RegExp | null): React.ReactNode {
  if (!re) return text;
  const out: React.ReactNode[] = [];
  let last = 0;
  re.lastIndex = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <mark key={`${m.index}-${out.length}`} style={markStyle}>
        {m[0]}
      </mark>,
    );
    last = m.index + m[0].length;
    if (m[0].length === 0) re.lastIndex += 1; // guard against zero-width loops
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const frame: React.CSSProperties = {
  width: "100%",
  display: "flex",
  flexDirection: "column",
  minHeight: 0,
  background: "var(--paper)",
  borderRight: "1px solid var(--paper-3)",
};

const header: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "8px 12px",
  borderBottom: "1px solid var(--paper-3)",
};

const fieldWrap: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  border: "1px solid var(--paper-3)",
  borderRadius: "var(--radius-btn)",
  background: "var(--white)",
  padding: "0 4px 0 0",
};

const input: React.CSSProperties = {
  flex: 1,
  height: 26,
  padding: "0 8px",
  border: "none",
  outline: "none",
  background: "transparent",
  fontSize: 12,
  color: "var(--text-paper)",
  minWidth: 0,
};

const iconToggle: React.CSSProperties = {
  height: 22,
  borderRadius: 4,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  cursor: "pointer",
  background: "transparent",
};

const fileHeader: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  width: "100%",
  padding: "4px 10px",
  background: "transparent",
  fontSize: 12,
  textAlign: "left",
  color: "var(--text-paper)",
};

const countBadge: React.CSSProperties = {
  fontSize: 10,
  minWidth: 16,
  textAlign: "center",
  padding: "0 5px",
  borderRadius: 8,
  background: "var(--paper-3)",
  color: "var(--text-paper-d)",
};

const matchRow: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 8,
  width: "100%",
  padding: "2px 10px 2px 28px",
  background: "transparent",
  fontSize: 12,
  textAlign: "left",
  color: "var(--text-paper-d)",
  fontFamily: "var(--font-mono)",
};

const lineNo: React.CSSProperties = {
  color: "var(--text-paper-d2)",
  fontSize: 10,
  minWidth: 22,
  textAlign: "right",
  flexShrink: 0,
};

const lineText: React.CSSProperties = {
  whiteSpace: "pre",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const markStyle: React.CSSProperties = {
  background: "var(--accent-soft)",
  color: "var(--accent-h)",
  borderRadius: 2,
};

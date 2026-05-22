import { useEffect, useState } from "react";
import { api, type FileContent, type FileInfo } from "./api";

interface Props {
  workspaceId: string;
  /** Bumping this number forces a re-fetch (e.g. after a write_file tool). */
  refreshTick: number;
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function FileBrowser({ workspaceId, refreshTick }: Props) {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<FileContent | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);

  // List refresh, also reacts to refreshTick bumps from Chat.
  useEffect(() => {
    let cancelled = false;
    setListLoading(true);
    api
      .listFiles(workspaceId)
      .then((items) => {
        if (cancelled) return;
        setFiles(items);
        setListError(null);
        // If the selected file vanished, clear the right pane.
        if (selected && !items.some((f) => f.path === selected)) {
          setSelected(null);
          setContent(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setListError(String(err));
      })
      .finally(() => {
        if (!cancelled) setListLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // selected intentionally left out: list refresh shouldn't re-run when
    // user clicks a file. The cleanup above handles the vanished-file case.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, refreshTick]);

  // Workspace switch resets the right pane.
  useEffect(() => {
    setSelected(null);
    setContent(null);
    setContentError(null);
  }, [workspaceId]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setContentLoading(true);
    setContentError(null);
    api
      .readFile(workspaceId, selected)
      .then((c) => {
        if (!cancelled) setContent(c);
      })
      .catch((err: unknown) => {
        if (!cancelled) setContentError(String(err));
      })
      .finally(() => {
        if (!cancelled) setContentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, selected]);

  return (
    <aside className="file-browser">
      <div className="file-list">
        <div className="file-list-header">Files</div>
        {listLoading && <div className="ws-status">Loading…</div>}
        {listError && <div className="ws-status ws-error">{listError}</div>}
        {!listLoading && files.length === 0 && !listError && (
          <div className="ws-status">No files yet.</div>
        )}
        <ul>
          {files.map((f) => (
            <li key={f.path}>
              <button
                type="button"
                className={selected === f.path ? "file-item active" : "file-item"}
                onClick={() => setSelected(f.path)}
              >
                <span className="file-path">{f.path}</span>
                <span className="file-size">{humanSize(f.size)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="file-view">
        {!selected && <div className="ws-status">Pick a file to preview.</div>}
        {selected && contentLoading && <div className="ws-status">Loading {selected}…</div>}
        {selected && contentError && (
          <div className="ws-status ws-error">{contentError}</div>
        )}
        {selected && content && content.kind === "binary" && (
          <div className="ws-status">
            {content.path} — binary, {humanSize(content.size)}
          </div>
        )}
        {selected && content && content.kind === "text" && (
          <>
            <div className="file-view-header">
              {content.path} <span className="file-size">{humanSize(content.size)}</span>
            </div>
            <pre className="file-view-body">{content.text}</pre>
          </>
        )}
      </div>
    </aside>
  );
}

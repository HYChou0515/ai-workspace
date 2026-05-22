import { useEffect, useState } from "react";
import { api, type Workspace } from "./api";

interface Props {
  activeId: string | null;
  onSelect: (workspaceId: string) => void;
}

export function WorkspaceList({ activeId, onSelect }: Props) {
  const [items, setItems] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .listWorkspaces()
      .then((ws) => {
        if (cancelled) return;
        setItems(ws);
        setError(null);
        if (!activeId && ws.length > 0) {
          onSelect(ws[0].resource_id);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeId, onSelect]);

  async function submit() {
    const name = newName.trim();
    if (!name || submitting) return;
    setSubmitting(true);
    try {
      const ws = await api.createWorkspace({ name, description: newDesc.trim() });
      setItems((prev) => [ws, ...prev]);
      onSelect(ws.resource_id);
      setNewName("");
      setNewDesc("");
      setAdding(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <aside className="workspace-list">
      <div className="workspace-list-header">
        <h2>Workspaces</h2>
        <button
          type="button"
          className="ws-new"
          onClick={() => setAdding((v) => !v)}
          aria-expanded={adding}
        >
          {adding ? "Cancel" : "+ New"}
        </button>
      </div>

      {adding && (
        <form
          className="ws-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Name"
            disabled={submitting}
          />
          <input
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            placeholder="Description (optional)"
            disabled={submitting}
          />
          <button type="submit" disabled={!newName.trim() || submitting}>
            {submitting ? "Creating…" : "Create"}
          </button>
        </form>
      )}

      {loading && <div className="ws-status">Loading…</div>}
      {error && <div className="ws-status ws-error">{error}</div>}

      {!loading && items.length === 0 && !error && (
        <div className="ws-status">No workspaces. Create one to start.</div>
      )}

      <ul>
        {items.map((ws) => (
          <li key={ws.resource_id}>
            <button
              type="button"
              className={ws.resource_id === activeId ? "ws-item active" : "ws-item"}
              onClick={() => onSelect(ws.resource_id)}
              title={ws.description || ws.name}
            >
              <span className="ws-name">{ws.name}</span>
              {ws.description && <span className="ws-desc">{ws.description}</span>}
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

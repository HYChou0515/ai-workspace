/**
 * The per-item environment variables panel.
 *
 * The item's `env_vars` are handed to the tools its agent runs — API keys and
 * the like. This edits them; the backend renders them into the sandbox each
 * turn and the tool launchers export them.
 *
 * Values are shown in PLAIN TEXT, deliberately. Masking would read as
 * protection it cannot deliver: anyone who can converse with this item's agent
 * can have it read the delivered file, so the only thing a mask would actually
 * remove is the ability to spot a mistyped key.
 *
 * Rows are edited as a LIST and collapsed into the stored map on save. A map
 * cannot hold two rows with the same name, so a duplicate is refused up front
 * rather than silently dropping one of them at save time.
 */
import { useState } from "react";

import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { ModalShell } from "./ModalShell";

type Row = { name: string; value: string };

/** The rows as they will be stored. Nameless rows are dropped — pressing Add and
 * changing your mind is common, and a blank row must not become a variable
 * nobody can find again. */
export function toEnvVars(rows: Row[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) {
    if (r.name.trim()) out[r.name.trim()] = r.value;
  }
  return out;
}

/** The first name used by more than one row, or "" when there is none. */
export function firstDuplicate(rows: Row[]): string {
  const seen = new Set<string>();
  for (const r of rows) {
    const name = r.name.trim();
    if (!name) continue;
    if (seen.has(name)) return name;
    seen.add(name);
  }
  return "";
}

const input: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  padding: "5px 8px",
  border: "1px solid var(--paper-3)",
  borderRadius: 6,
  background: "var(--paper)",
  color: "var(--text-paper)",
  fontSize: pxToRem(12),
};

export function EnvVarsModal({
  envVars,
  onSave,
  onClose,
}: {
  envVars: Record<string, string>;
  onSave: (next: Record<string, string>) => void | Promise<void>;
  onClose: () => void;
}) {
  const t = useT();
  const [rows, setRows] = useState<Row[]>(() =>
    Object.entries(envVars).map(([name, value]) => ({ name, value })),
  );
  const [error, setError] = useState("");

  const edit = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = () => {
    const dup = firstDuplicate(rows);
    if (dup) {
      setError(t("env.duplicate", { name: dup }));
      return;
    }
    setError("");
    void onSave(toEnvVars(rows));
  };

  return (
    <ModalShell
      onClose={onClose}
      ariaLabel={t("env.title")}
      data-testid="env-modal"
      width={520}
      maxWidth="92vw"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <strong style={{ fontSize: pxToRem(14) }}>{t("env.title")}</strong>
      <p style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)", lineHeight: 1.5 }}>
        {t("env.desc")}
      </p>

      {rows.length === 0 ? (
        <p
          data-testid="env-empty"
          style={{ margin: 0, fontSize: pxToRem(12), color: "var(--text-paper-d)" }}
        >
          {t("env.empty")}
        </p>
      ) : (
        <div
          className="scrollable"
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            overflowY: "auto",
            maxHeight: "50vh",
          }}
        >
          {rows.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                type="text"
                aria-label={t("env.name")}
                value={r.name}
                onChange={(e) => edit(i, { name: e.target.value })}
                style={{ ...input, flex: "0 1 40%" }}
              />
              {/* `type="text"`, never `password`: see the note at the top. */}
              <input
                type="text"
                aria-label={t("env.value")}
                value={r.value}
                onChange={(e) => edit(i, { value: e.target.value })}
                style={input}
              />
              <button
                type="button"
                className="btn"
                data-variant="secondary"
                data-size="sm"
                data-testid="env-delete"
                aria-label={t("env.delete", { name: r.name })}
                onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {error ? (
        <p data-testid="env-error" style={{ margin: 0, fontSize: pxToRem(12), color: "var(--err)" }}>
          {error}
        </p>
      ) : null}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-add"
          style={{ marginRight: "auto" }}
          onClick={() => setRows((rs) => [...rs, { name: "", value: "" }])}
        >
          {t("env.add")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-cancel"
          onClick={onClose}
        >
          {t("env.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-testid="env-save"
          onClick={save}
        >
          {t("env.save")}
        </button>
      </div>
    </ModalShell>
  );
}

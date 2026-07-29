/**
 * The per-item environment variables panel.
 *
 * The item's `env_vars` are handed to the tools its agent runs — API keys and
 * the like. This edits them; the backend renders them into the sandbox each
 * turn and the tool launchers export them.
 *
 * ONE text box holding the whole set as `.env` text, not a row per variable.
 * What people actually do with these is paste a block in from somewhere else —
 * a colleague, a password manager, another project's `.env` — and a row editor
 * turns that into one Add plus two clicks per line. It also makes the format
 * the same one they already have on their disk, so Import is a convenience
 * rather than the only way in.
 *
 * Storage is unchanged: the text is parsed into `dict[str, str]` on save
 * (`lib/envFile.ts`). Two consequences worth knowing rather than hiding:
 * comments and blank lines are not stored, so they do not survive a reopen; and
 * a name written twice keeps the last, which is dotenv's own rule and is at
 * least visible here, both lines being on screen.
 *
 * Nothing is masked, deliberately. Masking would read as protection it cannot
 * deliver: anyone who can converse with this item's agent can have it read the
 * delivered file, so the only thing a mask removes is the ability to spot a
 * mistyped key.
 */
import { useRef, useState } from "react";

import { mergeEnv, parseEnvText, toEnvText } from "../lib/envFile";
import { useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { ModalShell } from "./ModalShell";

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
  const [text, setText] = useState(() => toEnvText(envVars));
  const fileRef = useRef<HTMLInputElement>(null);

  /** Import MERGES into what is in the BOX, not into the last saved state: the
   * box is what the user is looking at, and importing on top of something they
   * cannot see would be a different operation than it appears to be. */
  const importFile = async (file: File) => {
    setText(toEnvText(mergeEnv(parseEnvText(text), parseEnvText(await file.text()))));
  };

  /** Export what is in the box, unsaved edits included — a download that
   * silently disagreed with the panel would be worse than none. Straight to the
   * browser, never into the workspace: a file there is one the agent can read. */
  const exportFile = () => {
    const url = URL.createObjectURL(
      new Blob([toEnvText(parseEnvText(text))], { type: "text/plain" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = ".env";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
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

      <textarea
        data-testid="env-text"
        aria-label={t("env.title")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"FOO=BAR\nBAZ=HOO"}
        spellCheck={false}
        rows={10}
        style={{
          width: "100%",
          boxSizing: "border-box",
          padding: "8px 10px",
          border: "1px solid var(--paper-3)",
          borderRadius: 6,
          background: "var(--paper)",
          color: "var(--text-paper)",
          // Monospace: these are keys, and a column of them is proofread by
          // eye. Proportional type hides the difference between l/1 and O/0.
          fontFamily: "var(--font-mono, ui-monospace, monospace)",
          fontSize: pxToRem(12),
          lineHeight: 1.6,
          resize: "vertical",
          whiteSpace: "pre",
          overflowWrap: "normal",
          overflowX: "auto",
        }}
      />

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 2 }}>
        <input
          ref={fileRef}
          type="file"
          accept=".env,text/plain"
          data-testid="env-import"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void importFile(f);
            e.target.value = ""; // so re-picking the same file fires again
          }}
        />
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-import-button"
          onClick={() => fileRef.current?.click()}
        >
          {t("env.import")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="secondary"
          data-size="sm"
          data-testid="env-export"
          style={{ marginRight: "auto" }}
          onClick={exportFile}
        >
          {t("env.export")}
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
        <button type="button" className="btn" data-size="sm" data-testid="env-save" onClick={() => void onSave(parseEnvText(text))}>
          {t("env.save")}
        </button>
      </div>
    </ModalShell>
  );
}

/**
 * "New collection" modal. A segmented control picks the kind (#355):
 *
 *  - **Documents** — name + description + retrieval toggles (the #50 default).
 *  - **Code repository** — point at a git repo; the backend clones it and builds
 *    the wiki from source (#281). Captures the Git URL (required, http(s) only —
 *    file:// / ssh:// are rejected client-side), an optional Advanced section
 *    (branch + access token), and forces both retrieval modes on. The name is
 *    suggested from the repo URL until the user edits it.
 *
 * The parent owns the create mutation and passes `onCreate`. Closes on Escape /
 * backdrop / Cancel.
 */

import { useEffect, useState } from "react";

import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { RetrievalToggles } from "./RetrievalToggles";

export type NewCollectionMode = "documents" | "code";

export type NewCollectionOpts = {
  useRag: boolean;
  useWiki: boolean;
  gitUrl?: string;
  gitBranch?: string;
  gitToken?: string;
};

/** Only http(s) remotes are accepted from the web form (file:// / ssh:// are a
 * server-local / multi-tenant footgun — #355). */
const HTTP_URL = /^https?:\/\/\S+/i;

/** Suggest a collection name from a git URL: the last path segment, minus a
 * trailing `.git`. `https://github.com/o/ai-workspace.git` → `ai-workspace`. */
export function repoNameFromUrl(url: string): string {
  const segment = url.trim().replace(/\/+$/, "").split("/").pop() ?? "";
  return segment.replace(/\.git$/i, "");
}

export function NewCollectionModal({
  open,
  onClose,
  onCreate,
  busy = false,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, description: string, opts: NewCollectionOpts) => void;
  busy?: boolean;
}) {
  const t = useT();
  const [mode, setMode] = useState<NewCollectionMode>("documents");
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [description, setDescription] = useState("");
  // Retrieval modes (#50): chunk search on by default, the LLM wiki opt-in.
  const [useRag, setUseRag] = useState(true);
  const [useWiki, setUseWiki] = useState(false);
  // Code repository (#355).
  const [gitUrl, setGitUrl] = useState("");
  const [gitBranch, setGitBranch] = useState("");
  const [gitToken, setGitToken] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Reset every time the modal opens.
  useEffect(() => {
    if (open) {
      setMode("documents");
      setName("");
      setNameEdited(false);
      setDescription("");
      setUseRag(true);
      setUseWiki(false);
      setGitUrl("");
      setGitBranch("");
      setGitToken("");
      setShowAdvanced(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const isCode = mode === "code";
  const urlInvalid = isCode && gitUrl.trim().length > 0 && !HTTP_URL.test(gitUrl.trim());
  const canCreate =
    name.trim().length > 0 &&
    !busy &&
    (isCode
      ? HTTP_URL.test(gitUrl.trim()) // code: a valid http(s) URL is required
      : useRag || useWiki); // documents: at least one retrieval mode

  // Editing the Git URL refreshes the suggested name until the user types one.
  const onGitUrlChange = (v: string) => {
    setGitUrl(v);
    if (!nameEdited) setName(repoNameFromUrl(v));
  };
  const onNameChange = (v: string) => {
    setName(v);
    setNameEdited(true);
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canCreate) return;
    onCreate(
      name.trim(),
      description.trim(),
      isCode
        ? {
            useRag: true,
            useWiki: true,
            gitUrl: gitUrl.trim(),
            gitBranch: gitBranch.trim() || undefined,
            gitToken: gitToken.trim() || undefined,
          }
        : { useRag, useWiki },
    );
  };

  return (
    <div className="kb-modal" role="presentation" onClick={onClose}>
      <form
        className="kb-modal__card"
        role="dialog"
        aria-modal
        aria-label="New collection"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <header className="kb-modal__head">
          <div className="caps">Knowledge base</div>
          <h2 className="kb-modal__title">New collection</h2>
        </header>

        <div className="kb-modal__body">
          <div className="kb-segmented" role="group" aria-label="Collection kind">
            <button
              type="button"
              className={`kb-segmented__opt${!isCode ? " is-active" : ""}`}
              aria-pressed={!isCode}
              onClick={() => setMode("documents")}
            >
              Documents
            </button>
            <button
              type="button"
              className={`kb-segmented__opt${isCode ? " is-active" : ""}`}
              aria-pressed={isCode}
              onClick={() => setMode("code")}
            >
              Code repository
            </button>
          </div>

          {isCode && (
            <label className="kb-field">
              <span className="kb-field__label">
                Git URL<span className="kb-field__req"> *</span>
              </span>
              <input
                className="kb-input"
                placeholder="https://github.com/owner/repo.git"
                value={gitUrl}
                onChange={(e) => onGitUrlChange(e.target.value)}
              />
              {urlInvalid && (
                <span className="kb-field__error" role="alert">
                  Enter an http(s):// URL (local file:// / ssh:// aren't supported here).
                </span>
              )}
            </label>
          )}

          <label className="kb-field">
            <span className="kb-field__label">
              Name<span className="kb-field__req"> *</span>
            </span>
            <input
              className="kb-input"
              // biome-ignore lint/a11y/noAutofocus: the primary input for Documents mode
              autoFocus={!isCode}
              placeholder="New collection name…"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
            />
          </label>
          <label className="kb-field">
            <span className="kb-field__label">Description</span>
            <textarea
              className="kb-input kb-textarea"
              placeholder="What lives in this collection?"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>

          {isCode ? (
            <div className="kb-field">
              <button
                type="button"
                className="kb-advanced-toggle"
                aria-expanded={showAdvanced}
                onClick={() => setShowAdvanced((v) => !v)}
              >
                <Icon name={showAdvanced ? "chev_d" : "chev_r"} size={12} /> Advanced
              </button>
              {showAdvanced && (
                <div className="kb-advanced">
                  <label className="kb-field">
                    <span className="kb-field__label">Branch</span>
                    <input
                      className="kb-input"
                      placeholder="(default branch)"
                      value={gitBranch}
                      onChange={(e) => setGitBranch(e.target.value)}
                    />
                  </label>
                  <label className="kb-field">
                    <span className="kb-field__label">Access token</span>
                    <input
                      className="kb-input"
                      type="password"
                      placeholder="for a private repo"
                      autoComplete="off"
                      value={gitToken}
                      onChange={(e) => setGitToken(e.target.value)}
                    />
                  </label>
                </div>
              )}
            </div>
          ) : (
            <fieldset className="kb-field" style={{ border: 0, margin: 0, padding: 0 }}>
              <span className="kb-field__label">{t("kb.retrieval.title")}</span>
              <RetrievalToggles
                docSearch={useRag}
                wiki={useWiki}
                onChange={({ docSearch, wiki }) => {
                  setUseRag(docSearch);
                  setUseWiki(wiki);
                }}
              />
            </fieldset>
          )}
        </div>

        <footer className="kb-modal__foot">
          <button type="button" className="kb-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="kb-btn kb-btn--primary" disabled={!canCreate}>
            <Icon name="plus" size={13} /> Create
          </button>
        </footer>
      </form>
    </div>
  );
}

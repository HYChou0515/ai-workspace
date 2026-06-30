/**
 * #355: edit a code collection's git connection — change the branch or rotate
 * the access token. The token field is intentionally EMPTY (the stored PAT is
 * write-only and never echoed back): leave it blank to keep the current token,
 * type a new one to rotate it. Saving PATCHes the Collection (`git_branch`, and
 * `git_token` only when a new one was typed); a code collection re-syncs from the
 * new settings on the next Sync now / daily sync.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { type KbApi, type KbCollection } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";

export function CodeConnectionEditor({
  collection,
  client,
  onClose,
}: {
  collection: KbCollection;
  client: KbApi;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [branch, setBranch] = useState(collection.git_branch ?? "");
  const [token, setToken] = useState("");

  const saveMut = useMutation({
    mutationFn: () =>
      client.updateCollection(collection.resource_id, {
        // null ⇒ remote default branch.
        git_branch: branch.trim() || null,
        // Only send the token when rotating it — blank keeps the stored one.
        ...(token.trim() ? { git_token: token.trim() } : {}),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.kb.collections });
      onClose();
    },
  });

  return (
    <div
      style={{
        margin: "4px 0 8px",
        padding: 14,
        border: "1px solid var(--paper-3)",
        borderRadius: 8,
        background: "var(--paper-2)",
      }}
    >
      <div
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}
      >
        <span className="caps">Git connection</span>
        <button type="button" className="kb-btn" aria-label="Close git connection" onClick={onClose}>
          <Icon name="x" size={12} />
        </button>
      </div>

      <div className="kb-field" style={{ marginBottom: 4 }}>
        <span className="kb-field__label">Git URL</span>
        <input className="kb-input" value={collection.git_url ?? ""} readOnly disabled />
      </div>
      <label className="kb-field">
        <span className="kb-field__label">Branch</span>
        <input
          className="kb-input"
          placeholder="(default branch)"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
        />
      </label>
      <label className="kb-field">
        <span className="kb-field__label">Access token</span>
        <input
          className="kb-input"
          type="password"
          autoComplete="off"
          placeholder="leave blank to keep the current token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </label>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
        <button type="button" className="kb-btn" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="kb-btn kb-btn--primary"
          disabled={saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          Save
        </button>
      </div>
    </div>
  );
}

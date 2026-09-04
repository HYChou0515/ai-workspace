/**
 * Who may manage this item — apart from the role ladder, on purpose.
 *
 * The ladder is nested: each rung is the one below it plus a little more, and a
 * dropdown says so. This grant is not more of the same. It lets someone regrant
 * the item to anybody, and — since per-item environment sizing — decide how much
 * of the OWNER's quota the item spends. Both consequences would disappear into
 * "one step past Collaborator" if it were a sixth rung, which is why it is a
 * separate control with the consequence written next to it.
 *
 * People only. The backend has honoured `group:` subjects here since #608 and
 * they round-trip untouched (`withItemManagers` preserves them) — offering to
 * edit them is a later question, silently dropping one an operator set by hand
 * is not an option.
 */

import { useState } from "react";

import { useT } from "../lib/i18n";

export function ItemShareManagers({
  managers,
  onChange,
}: {
  managers: string[];
  onChange: (next: string[]) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState("");

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const id = draft.trim();
    // A duplicate is not rejected by the backend — it would simply sit in the
    // list twice, with two revoke buttons that each look broken.
    if (!id || managers.includes(id)) return;
    onChange([...managers, id]);
    setDraft("");
  };

  return (
    <div className="item-share-managers" data-testid="item-share-managers">
      <h4>{t("itemshare.managers.heading")}</h4>
      {/* Said BEFORE the control, not after: this is the sentence someone needs
          in order to decide, and underneath the input it would be an epilogue. */}
      <p data-testid="managers-consequence" className="detail">
        {t("itemshare.managers.consequence")}
      </p>

      <ul>
        {managers.map((id) => (
          <li key={id}>
            <span>{id}</span>
            <button
              type="button"
              data-testid="manager-remove"
              onClick={() => onChange(managers.filter((m) => m !== id))}
            >
              {t("itemshare.managers.remove")}
            </button>
          </li>
        ))}
      </ul>

      <form data-testid="manager-add-form" onSubmit={add}>
        <input
          data-testid="manager-add"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("itemshare.managers.add")}
          aria-label={t("itemshare.managers.add")}
        />
      </form>
    </div>
  );
}

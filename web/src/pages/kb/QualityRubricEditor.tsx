// #105: edit a collection's quality rubric — the user-authored criteria the
// index-time judge scores each doc against. Mirrors the #90 WikiGuidanceEditor:
// a textarea + a save mutation that PATCHes the Collection. Blank ⇒ scoring off.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { kbApi, type KbApi } from "../../api/kb";
import { qk } from "../../api/queryKeys";
import { Icon } from "../../components/Icon";
import { useT } from "../../lib/i18n";
import { pxToRem } from "../../lib/pxToRem";

export function QualityRubricEditor({
  collectionId,
  rubric,
  client = kbApi,
}: {
  collectionId: string;
  rubric: string;
  client?: KbApi;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [draft, setDraft] = useState(rubric);
  useEffect(() => setDraft(rubric), [rubric]);

  const saveMut = useMutation({
    mutationFn: () => client.updateCollection(collectionId, { quality_rubric: draft }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.kb.collections }),
  });
  const dirty = draft !== rubric;

  return (
    <section
      aria-label={t("kb.quality.rubric.title")}
      style={{
        textAlign: "left",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        padding: 16,
        background: "var(--paper-2)",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        maxWidth: 560,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Icon name="sparkle" size={13} color="var(--accent-h)" />
        <span className="caps" style={{ fontSize: pxToRem(11) }}>
          {t("kb.quality.rubric.title")}
        </span>
      </div>
      <p
        style={{
          fontSize: pxToRem(11.5),
          color: "var(--text-paper-d)",
          margin: "2px 0 6px",
          lineHeight: 1.45,
        }}
      >
        {t("kb.quality.rubric.hint")}
      </p>
      <textarea
        aria-label={t("kb.quality.rubric.title")}
        value={draft}
        placeholder={t("kb.quality.rubric.placeholder")}
        onChange={(e) => setDraft(e.target.value)}
        style={{
          width: "100%",
          minHeight: 96,
          resize: "vertical",
          padding: "8px 10px",
          borderRadius: 8,
          border: "1px solid var(--paper-3)",
          background: "var(--paper)",
          font: "inherit",
          fontSize: pxToRem(13),
          lineHeight: 1.5,
          boxSizing: "border-box",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
        <button
          type="button"
          className="kb-btn kb-btn--primary"
          disabled={!dirty || saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isSuccess && !dirty
            ? t("kb.quality.rubric.saved")
            : t("kb.quality.rubric.save")}
        </button>
        {saveMut.isError && (
          <span role="alert" style={{ fontSize: pxToRem(12), color: "var(--warn)" }}>
            {t("kb.quality.rubric.save")}
          </span>
        )}
      </div>
    </section>
  );
}

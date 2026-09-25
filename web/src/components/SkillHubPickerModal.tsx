/**
 * 「從 skill hub 裝」— pick a published skill and install it into THIS item
 * (`docs/plan-skill-hub.md`, D2: installing is the item's).
 *
 * The list is the server's, asked for with this item's App so every row
 * carries the告知 the plan requires (Q1/Q2): which of the tools it mentions
 * this App does not have. Shown, never enforced — the person decides. The
 * install goes through the panel's own door (`POST …/skills/install`), which
 * shares its core and its refusals with the agent's `install_skill` tool: a
 * folder of that name already here is a 409 carrying the fact (whose copy,
 * which path), which `describeRefusal` words for the person.
 *
 * That refusal is also known BEFORE the press (plan-skill-hub-ui-polish D8):
 * the panel hands over the names whose files are here (`taken`, the same
 * predicate as its Download), and a row of such a name is marked 「已有同名
 * skill」 with its Install disabled — the decision's information at the
 * decision point. It is a second reading of the fact the route refuses on
 * (the folder exists), pinned to the route's by a parity test
 * (`tests/api/test_skill_hub_panel.py`) rather than kept alike by hand.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import { type SkillHubApi, type SkillHubCard, skillHubApi } from "../api/skillHub";
import { useT } from "../lib/i18n";
import { describeRefusal } from "../lib/skillHubRefusal";
import { pxToRem } from "../lib/pxToRem";
import { AppTag } from "./AppTag";
import { ModalActions } from "./ModalActions";
import { ModalShell } from "./ModalShell";

export function SkillHubPickerModal({
  slug,
  itemId,
  taken,
  onInstalled,
  onClose,
  client = skillHubApi,
}: {
  slug: string;
  itemId: string;
  /** Names of this item's skills whose files are HERE (`filesHere`) — the
   * names an install would be refused for. */
  taken: ReadonlySet<string>;
  /** Called with the installed skill's name; the caller refreshes its list. */
  onInstalled: (name: string) => void;
  onClose: () => void;
  client?: Pick<SkillHubApi, "list" | "install">;
}) {
  const t = useT();
  const titleId = useId();
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setQ(query.trim()), 250);
    return () => clearTimeout(id);
  }, [query]);
  const listQ = useQuery({
    queryKey: qk.skillHub(q, false, slug),
    queryFn: () => client.list(q, false, slug),
  });
  const [failure, setFailure] = useState<string | null>(null);
  const install = useMutation({
    mutationFn: (entryId: string) => client.install(slug, itemId, entryId),
    onSuccess: (res) => onInstalled(res.name),
    onError: (e) => setFailure(describeRefusal(e, t)),
    // The refusal is shown right here (`failure`), so the query client's
    // global write-failure toast must not report it a second time — under a
    // title that is not even true, nothing was being saved
    // (plan-skill-hub-ui-polish D3).
    meta: { silentError: true },
  });
  // Roots and forks flattened for the picker: a fork is one more thing to
  // install, and the list route already put each under its root.
  const rows = (listQ.data ?? []).flatMap((e) => [e, ...e.forks]);

  return (
    // A picker that applies nothing until Install is pressed: nothing to lose,
    // so a stray click closes it (#779), like the other live pickers.
    <ModalShell
      onClose={onClose}
      labelledBy={titleId}
      width={560}
      maxWidth="92vw"
      closeOnBackdrop
      zIndex="var(--z-dialog)"
      data-testid="skill-hub-picker"
      panelStyle={{ padding: 18, display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}
    >
      <h2 id={titleId} className="modal-title">
        {t("skills.fromHub")}
      </h2>
      <p style={{ margin: 0, fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>

        {t("skills.fromHub.intro")}
      </p>
      <p style={{ margin: 0, fontSize: "var(--text-body-sm)" }}>
        <Link to="/skill-hub">{t("skills.fromHub.browse")}</Link>
      </p>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t("skillHub.search")}
        aria-label={t("skillHub.search")}
        className="input input--block"
      />
      {failure ? (
        <p className="error" role="alert" style={{ margin: 0, fontSize: "var(--text-body-sm)" }}>
          {t("skillHub.failed", { reason: failure })}
        </p>
      ) : null}
      <div className="scrollable" style={{ overflowY: "auto", flex: 1, minHeight: 0 }}>
        {listQ.isError ? (
          <p className="error" role="alert">
            {t("skillHub.error")}
          </p>
        ) : listQ.isPending ? (
          <p style={{ color: "var(--text-paper-d)" }}>{t("skillHub.loading")}</p>
        ) : rows.length === 0 ? (
          <p data-testid="skill-hub-picker-empty" style={{ color: "var(--text-paper-d)" }}>
            {q ? t("skillHub.noMatch") : t("skills.fromHub.none")}
          </p>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {rows.map((row) => (
              <PickRow
                key={row.id}
                row={row}
                taken={taken.has(row.name)}
                busy={install.isPending}
                onInstall={() => {
                  setFailure(null);
                  install.mutate(row.id);
                }}
              />
            ))}
          </ul>
        )}
      </div>
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.cancel")}
        </button>
      </ModalActions>
    </ModalShell>
  );
}

function PickRow({
  row,
  taken,
  busy,
  onInstall,
}: {
  row: SkillHubCard;
  taken: boolean;
  busy: boolean;
  onInstall: () => void;
}) {
  const t = useT();
  return (
    <li
      data-testid={`pick-${row.id}`}
      style={{
        display: "flex",
        // The panel row's shape (D13): the controls are one cluster that
        // drops under the text when the row is too narrow for both.
        flexWrap: "wrap",
        alignItems: "center",
        gap: 8,
        padding: "8px 10px",
        border: "1px solid var(--paper-3)",
        borderRadius: "var(--radius-card)",
        background: "var(--white)",
      }}
    >
      {/* A real basis, not `flex: 1` alone: wrapping is decided on the
          hypothetical size, and a column that may be 0 wide never lets the
          cluster wrap. */}
      <div style={{ flex: "1 1 200px", minWidth: 0, fontSize: pxToRem(13) }}>
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
          <span>
            <span style={{ color: "var(--text-paper-d)" }}>{row.owner}/</span>
            <strong>{row.name}</strong>
          </span>
          <AppTag slug={row.source_app} />
          {row.forked_from ? (
            <span style={{ fontSize: pxToRem(10), color: "var(--text-paper-d)" }}>fork</span>
          ) : null}
        </div>
        <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)", overflowWrap: "anywhere" }}>
          {row.description}
        </div>
        {row.missing_tools.length > 0 ? (
          // The告知 (plan Q1): named, on the row, never a block.
          <div
            data-testid={`pick-missing-${row.id}`}
            style={{ fontSize: pxToRem(11), color: "var(--warn)", marginTop: 2 }}
          >
            {t("skills.fromHub.missing", { tools: row.missing_tools.join(", ") })}
          </div>
        ) : null}
      </div>
      <div
        data-testid={`pick-actions-${row.id}`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flex: "none",
          marginLeft: "auto",
        }}
      >
        {taken ? (
          <span
            data-testid={`pick-taken-${row.id}`}
            style={{
              fontSize: pxToRem(11),
              color: "var(--text-paper-d)",
              whiteSpace: "nowrap",
            }}
          >
            {t("skills.fromHub.taken")}
          </span>
        ) : null}
        <button
          type="button"
          className="btn"
          data-size="sm"
          data-variant="primary"
          data-testid={`pick-install-${row.id}`}
          disabled={busy || taken}
          onClick={onInstall}
        >
          {t("skills.fromHub.install")}
        </button>
      </div>
    </li>
  );
}

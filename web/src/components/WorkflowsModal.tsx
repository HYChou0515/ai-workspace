import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import type { FileService } from "../api/fileService";
import { qk } from "../api/queryKeys";
import { TemplateConflictError, workflowTemplatesApi } from "../api/workflowTemplates";
import { workflowApi } from "../api/workflows";
import { SCHEDULES_PATH, type ScheduleRow, schedulesApi } from "../api/schedules";
import { WORKFLOWS_DIR } from "../api/workspaceWorkflows";
import { useItemSchedules } from "../hooks/useItemSchedules";
import { useWorkflowTemplates } from "../hooks/useWorkflowTemplates";
import { useWorkspaceWorkflows } from "../hooks/useWorkspaceWorkflows";
import { type MsgKey, useT } from "../lib/i18n";
import { pxToRem } from "../lib/pxToRem";
import { Icon } from "./Icon";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { useDialog } from "./Dialog";
import { ModalShell } from "./ModalShell";

/**
 * The Workflows panel (#323) — lists the workflows the user co-created with the agent in
 * THIS workspace (`.workflows/<id>.json`), since the IDE tree hides the dot-folder. A
 * workflow is DATA the platform interprets, not code, so it's safe to run: each row has a
 * **Run** button (the workspace self-serve trigger, P4). The whole `.workflows/` folder
 * downloads as a zip (hand it to the team to bake into the starting profile); a `.json`
 * imports back via the file routes. Creating one is a conversation — the empty state
 * points the user at the agent (the `author-workflow` skill).
 */
export function WorkflowsModal({
  slug,
  itemId,
  fileService,
  onClose,
  onRun,
}: {
  slug: string;
  itemId: string;
  fileService: FileService;
  onClose: () => void;
  /** Called with the new run's chat id after a successful launch, so the parent can
   * focus the run's chat. */
  onRun?: (chatId: string) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const dialog = useDialog();
  const workflows = useWorkspaceWorkflows(slug, itemId);
  const templates = useWorkflowTemplates(slug, itemId);
  const schedules = useItemSchedules(slug, itemId);
  const importRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  // #779: an import/apply in flight. Closing does not cancel it — it just takes
  // away the only place the result (or the failure) would have been shown.
  const attemptClose = useDirtyClose(busy, onClose);

  /** #520: pull a shipped template in. A name clash comes back as a 409 rather than
   * quietly overwriting, so we ask before replacing — the copy in the workspace may
   * already carry the user's edits. */
  const copyTemplate = async (id: string, title: string) => {
    setBusy(true);
    try {
      try {
        await workflowTemplatesApi.copy(slug, itemId, id);
      } catch (err) {
        if (!(err instanceof TemplateConflictError)) throw err;
        const choice = await dialog.confirm({
          title: t("templates.heading"),
          body: t("templates.replaceConfirm", { name: title || id }),
          actions: [
            { id: "replace", label: t("templates.replace"), variant: "danger" },
            { id: "cancel", label: t("workflows.close") },
          ],
        });
        if (choice !== "replace") return;
        await workflowTemplatesApi.copy(slug, itemId, id, { overwrite: true });
      }
      await qc.invalidateQueries({ queryKey: qk.workspaceWorkflows(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  const run = async (id: string) => {
    setBusy(true);
    try {
      const res = await workflowApi.startRun(slug, itemId, id);
      onRun?.(res.chat_id);
      onClose();
    } finally {
      setBusy(false);
    }
  };

  const downloadAll = async () => {
    const prep = await fileService.prepareDirDownload(WORKFLOWS_DIR);
    const a = document.createElement("a");
    a.href = fileService.dirDownloadUrl(prep.download_id, WORKFLOWS_DIR);
    a.download = prep.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const importFiles = async (files: FileList) => {
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        await fileService.writeFile(`${WORKFLOWS_DIR}/${f.name}`, await f.arrayBuffer());
      }
      await qc.invalidateQueries({ queryKey: qk.workspaceWorkflows(slug, itemId) });
      // A `schedules.json` is a legitimate thing to import — it lands in the
      // same folder — so the schedules section must not show the old rows.
      await qc.invalidateQueries({ queryKey: qk.itemSchedules(slug, itemId) });
      await qc.invalidateQueries({ queryKey: qk.files(itemId) });
    } finally {
      setBusy(false);
    }
  };

  /** Cancel one schedule: rewrite the file minus that row, through the ordinary
   * file write so it lands on the path the platform indexes. Every OTHER row is
   * kept exactly as written — the refused ones included — because a rewrite that
   * dropped them would cancel schedules nobody asked to cancel.
   *
   * From the file AS IT IS NOW, not from what this panel loaded: the query is a
   * cache with a 30s staleTime, and between the load and the click the agent's
   * `save_schedules` may have added a row. Rewriting from the cache would write
   * that row out of existence, silently — the exact failure the sentence above
   * promises to avoid. So: fetch, find the clicked row by what it SAYS (the
   * index may have shifted), and rewrite from that. A row that is already gone
   * means nothing to write. */
  const removeSchedule = async (row: ScheduleRow) => {
    const choice = await dialog.confirm({
      title: t("schedules.removeTitle"),
      body: t("schedules.removeConfirm", {
        what: `${describeSchedule(row.raw, t)} → ${row.run || "?"}`,
      }),
      actions: [
        { id: "remove", label: t("schedules.remove"), variant: "danger" },
        { id: "cancel", label: t("schedules.cancel") },
      ],
    });
    if (choice !== "remove") return;
    setBusy(true);
    try {
      const fresh = await qc.fetchQuery({
        queryKey: qk.itemSchedules(slug, itemId),
        queryFn: () => schedulesApi.list(slug, itemId),
        staleTime: 0,
      });
      const target = fresh.rows.find((r) => sameJson(r.raw, row.raw));
      if (target) {
        const kept = fresh.rows.filter((r) => r !== target).map((r) => r.raw);
        await fileService.writeFile(SCHEDULES_PATH, JSON.stringify({ schedules: kept }, null, 2));
        await qc.invalidateQueries({ queryKey: qk.files(itemId) });
      }
      await qc.invalidateQueries({ queryKey: qk.itemSchedules(slug, itemId) });
    } finally {
      setBusy(false);
    }
  };

  const list = workflows.data ?? [];
  const titleOf = (run: string) => list.find((w) => w.id === run)?.title || run;
  const sched = schedules.data;
  const showSchedules = !!sched && (sched.rows.length > 0 || sched.problems.length > 0);

  return (
    <ModalShell
      onClose={attemptClose}
      ariaLabel={t("workflows.title")}
      data-testid="workflows-modal"
      width={480}
      maxWidth="92vw"
      panelStyle={{
        padding: 18,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        minHeight: 0,
      }}
    >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Icon name="workflow" size={15} />
          <strong style={{ flex: 1 }}>{t("workflows.title")}</strong>
          <button
            type="button"
            aria-label={t("workflows.close")}
            onClick={attemptClose}
            style={{ border: "none", background: "transparent", cursor: "pointer" }}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        <p style={{ margin: 0, fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}>
          {t("workflows.intro")}
        </p>

        <div className="scrollable" style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {list.length === 0 ? (
            <p
              data-testid="workflows-empty"
              style={{ fontSize: "var(--text-body-sm)", color: "var(--text-paper-d)" }}
            >
              {t("workflows.empty")}
            </p>
          ) : (
            list.map((w) => (
              <div
                key={w.id}
                data-testid="workflow-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-btn)",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>
                    {w.title || w.id}
                  </div>
                  <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                    {t("workflows.steps", { n: w.phases.length })}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`workflow-run-${w.id}`}
                  aria-label={`${t("workflows.run")} ${w.title || w.id}`}
                  disabled={busy}
                  onClick={() => void run(w.id)}
                  style={pillBtn}
                >
                  <Icon name="play" size={12} /> {t("workflows.run")}
                </button>
              </div>
            ))
          )}
        </div>

        {showSchedules && (
          <div
            data-testid="schedules-section"
            style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}
          >
            <strong style={{ fontSize: "var(--text-body-sm)" }}>{t("schedules.heading")}</strong>
            <p style={{ margin: 0, fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
              {t("schedules.intro")}
            </p>
            {!sched.enabled && (
              <p
                data-testid="schedules-disabled"
                role="status"
                style={{ margin: 0, fontSize: pxToRem(11), color: "var(--err)" }}
              >
                {t("schedules.disabled")}
              </p>
            )}
            {!sched.indexed && (
              <p
                data-testid="schedules-unindexed"
                role="status"
                style={{ margin: 0, fontSize: pxToRem(11), color: "var(--err)" }}
              >
                {t("schedules.unindexed")}
              </p>
            )}
            {sched.problems.length > 0 && (
              <p
                data-testid="schedules-file-problems"
                style={{ margin: 0, fontSize: pxToRem(11), color: "var(--err)" }}
              >
                {t("schedules.fileProblems")} {sched.problems.join(" ")}
              </p>
            )}
            {sched.rows.map((row) => {
              const invalid = row.problems.length > 0;
              return (
                <div
                  key={row.index}
                  data-testid={`schedule-row-${row.index}`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "6px 8px",
                    border: "1px solid var(--paper-3)",
                    borderRadius: "var(--radius-btn)",
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>
                      {describeSchedule(row.raw, t)}
                      {" → "}
                      {invalid ? rawRun(row.raw) : titleOf(row.run)}
                    </div>
                    <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                      {invalid ? (
                        <span style={{ color: "var(--err)" }}>
                          {t("schedules.invalidRow")} {row.problems.join(" ")}
                        </span>
                      ) : !row.known ? (
                        <span data-testid={`schedule-unknown-${row.index}`} style={{ color: "var(--err)" }}>
                          {t("schedules.unknownWorkflow")}
                        </span>
                      ) : !row.runnable ? null : row.due_now ? (
                        t("schedules.nextSweep")
                      ) : (
                        t("schedules.next", { at: `${row.next_at} ${row.tz}` })
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    data-testid={`schedule-remove-${row.index}`}
                    aria-label={`${t("schedules.remove")} ${describeSchedule(row.raw, t)}`}
                    disabled={busy}
                    onClick={() => void removeSchedule(row)}
                    style={pillBtn}
                  >
                    <Icon name="x" size={12} /> {t("schedules.remove")}
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {(templates.data ?? []).length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
            <strong style={{ fontSize: "var(--text-body-sm)" }}>{t("templates.heading")}</strong>
            <p
              style={{
                margin: 0,
                fontSize: pxToRem(11),
                color: "var(--text-paper-d)",
              }}
            >
              {t("templates.intro")}
            </p>
            {(templates.data ?? []).map((tpl) => (
              <div
                key={tpl.id}
                data-testid="workflow-template-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  border: "1px solid var(--paper-3)",
                  borderRadius: "var(--radius-btn)",
                  // #520: an unusable template stays VISIBLE but reads as inert, so the
                  // user learns it exists and what would make it work.
                  opacity: tpl.compatible ? 1 : 0.55,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--text-body-sm)" }}>
                    {tpl.title || tpl.id}
                  </div>
                  <div style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
                    {tpl.compatible ? tpl.description : t("templates.unavailable")}
                  </div>
                </div>
                <button
                  type="button"
                  data-testid={`workflow-template-copy-${tpl.id}`}
                  aria-label={`${t("templates.copy")} ${tpl.title || tpl.id}`}
                  disabled={busy || !tpl.compatible}
                  title={tpl.compatible ? undefined : tpl.problems.join("; ")}
                  onClick={() => void copyTemplate(tpl.id, tpl.title)}
                  style={pillBtn}
                >
                  <Icon name="download" size={12} /> {t("templates.copy")}
                </button>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
          <button
            type="button"
            data-testid="workflows-download"
            disabled={list.length === 0}
            onClick={() => void downloadAll()}
            style={pillBtn}
          >
            <Icon name="download" size={12} /> {t("workflows.download")}
          </button>
          <button
            type="button"
            data-testid="workflows-import"
            disabled={busy}
            onClick={() => importRef.current?.click()}
            style={pillBtn}
          >
            <Icon name="upload" size={12} /> {t("workflows.import")}
          </button>
          <span style={{ fontSize: pxToRem(11), color: "var(--text-paper-d)" }}>
            {t("workflows.importHint")}
          </span>
          <input
            ref={importRef}
            type="file"
            accept=".json,application/json"
            multiple
            data-testid="workflows-import-input"
            style={{ display: "none" }}
            onChange={(e) => {
              const files = e.target.files;
              if (files && files.length) void importFiles(files);
              e.target.value = "";
            }}
          />
        </div>
    </ModalShell>
  );
}

/** The recurrence in the reader's words, from the row as written (`every`, `at`,
 * `dow`, `dom`, `n`, `tz`). Only vocabulary: when a row fires NEXT is computed on
 * the backend, by the same rule the sweep fires it by, and arrives as `next_at`. */
function describeSchedule(value: unknown, t: ReturnType<typeof useT>): string {
  // A row that is not an object has no fields to read; the backend has already
  // said so in `problems`, and the rewrite still carries the value as written.
  const raw: Record<string, unknown> =
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  // The parser's rule for every one of these is Python's `or`: a falsy value —
  // absent, null, "", 0, false — is the default. Mirrored exactly, so the
  // panel never shows "0 (UTC)" for a row the sweep runs daily at 00:00.
  const at = typeof raw.at === "string" && raw.at ? raw.at : "00:00";
  const tz = typeof raw.tz === "string" && raw.tz ? raw.tz : "UTC";
  const every = raw.every || "daily";
  let words: string;
  switch (every) {
    case "minutes":
      words = t("schedules.every.minutes", { n: Number(raw.n) || 0 });
      break;
    case "hourly":
      words = t("schedules.every.hourly");
      break;
    case "weekly": {
      const dow = typeof raw.dow === "string" ? raw.dow : "";
      const key = DOW_KEYS[dow];
      words = t("schedules.every.weekly", { dow: key ? t(key) : dow, at });
      break;
    }
    case "monthly":
      words = t("schedules.every.monthly", { dom: Number(raw.dom) || 0, at });
      break;
    case "daily":
      words = t("schedules.every.daily", { at });
      break;
    default:
      words = String(every);
  }
  return `${words} (${tz})`;
}

/**
 * Order-preserving JSON equality — the identity of a schedule row. NOT
 * `sameShape`: that one compares arrays as sets (right for grant lists, whose
 * order nobody arranges), and two rows that differ only in the order of an array
 * inside `with` are two DIFFERENT schedules to the sweep (`trigger_id_for`
 * fingerprints the payload as written), so Remove must tell them apart.
 * Object key order is not identity (the file was parsed, not diffed as text).
 */
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((x, i) => sameJson(x, b[i]))
    );
  }
  if (a !== null && b !== null && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a).sort();
    const kb = Object.keys(b).sort();
    return (
      ka.length === kb.length &&
      ka.every(
        (k, i) =>
          k === kb[i] &&
          sameJson((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
      )
    );
  }
  return false;
}

/** What a refused row SAID it would run, for the line that shows it. */
function rawRun(value: unknown): string {
  if (value !== null && typeof value === "object" && "run" in value) {
    return String((value as { run?: unknown }).run ?? "?");
  }
  return "?";
}

const DOW_KEYS: Record<string, MsgKey> = {
  mon: "schedules.dow.mon",
  tue: "schedules.dow.tue",
  wed: "schedules.dow.wed",
  thu: "schedules.dow.thu",
  fri: "schedules.dow.fri",
  sat: "schedules.dow.sat",
  sun: "schedules.dow.sun",
};

const pillBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  height: 24,
  padding: "0 8px",
  fontSize: pxToRem(11),
  borderRadius: "var(--radius-btn)",
  border: "1px solid var(--paper-3)",
  background: "var(--white)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

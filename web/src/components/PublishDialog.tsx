/**
 * The publish dialog (`docs/plan-wui-publish-and-agent-hub.md`, P4/P6).
 *
 * One dialog, two things to people: publishing a WUI always asks how copies
 * are shared (W2 — no default, each option with its cost beside it); an agent
 * is one copy per conversation (A2), so it does not ask, and offers a release
 * note instead (A9). Both ask who may use it (C9) and, when copies are made,
 * their sandbox size (C10), and show what a copy will hold (C11).
 *
 * UI guidance: a mutually exclusive choice is a radio group (NN/g, "Checkboxes
 * vs. Radio Buttons"); the way of sharing is deliberately NOT pre-selected — a
 * default would be a choice nobody made, and W2 says publishing always asks —
 * so the Publish button says why it is not yet available (NN/g, "Error
 * prevention", and "Defaults" on when not to set one). The cost sits beside
 * each option, where the choice is made (NN/g, "Recognition rather than recall").
 */

import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import {
  type Estimate,
  type PublishApi,
  type PublishChoices,
  type PublishKind,
  publishApi,
  type Reach,
  type Role,
  type Sharing,
  WUI_SHARINGS,
} from "../api/publish";
import { qk } from "../api/queryKeys";
import { useDirtyClose } from "../hooks/useDirtyClose";
import { usePickableGroups } from "../hooks/usePickableGroups";
import { useItemPeople } from "../hooks/useItemPeople";
import { type MsgKey, useT } from "../lib/i18n";
import { sameShape } from "../lib/sameShape";
import { ModalActions } from "./ModalActions";
import { ModalShell } from "./ModalShell";
import { UserPicker } from "./UserPicker";

/** A rough per-file cost for making a copy, borrowed from the closest thing
 * measured on this platform — a reaped workspace restored file by file, ~60 s
 * for 50 000 files (`docs/plan-archive-pack.md`). A copy is a store-to-store
 * copy, not that restore, so the dialog says "about"; it is a size cue, not a
 * promise. */
const MS_PER_FILE = 1.2;
/** Above this many files the dialog names the folders that hold them (C11). */
const MANY_FILES = 20_000;

export const EMPTY_CHOICES: PublishChoices = {
  sharing: "direct",
  reach: "me",
  subjects: [],
  role: "participant",
  cpu_cores: null,
  memory_bytes: null,
  note: "",
};

const GB = 1024 ** 3;

function sizeText(bytes: number): string {
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function PublishDialog({
  kind,
  slug,
  itemId,
  path,
  initial,
  republish = false,
  loadError,
  client = publishApi,
  onConfirm,
  onClose,
}: {
  kind: PublishKind;
  slug: string;
  itemId: string;
  path: string;
  /** What the view file's `publish:` already says (W5, A4); a WUI's way of
   * sharing counts as chosen only when it is there. */
  initial?: Partial<PublishChoices>;
  /** Published before: offer the release note — an agent's (A9), and a WUI's
   * that hands out copies (docs/plan-wui-copy-update.md D6). */
  republish?: boolean;
  /** How it is published now could not be read: said, and Publish is held —
   * publishing from a guess is how a re-publish reset who may use it. */
  loadError?: string;
  client?: Pick<PublishApi, "estimate">;
  onConfirm: (choices: PublishChoices) => void;
  onClose: () => void;
}) {
  const t = useT();
  const itemPeople = useItemPeople(slug, itemId);
  const titleId = useId();
  const groups = usePickableGroups();
  const start = {
    // Only a WUI's own ways count as chosen: a `publish:` naming an agent's
    // way would otherwise pre-select a choice the server refuses.
    sharing:
      kind === "agent"
        ? ("per_conversation" as Sharing)
        : initial?.sharing && WUI_SHARINGS.includes(initial.sharing)
          ? initial.sharing
          : null,
    reach: initial?.reach ?? EMPTY_CHOICES.reach,
    subjects: initial?.subjects ?? [],
    role: initial?.role ?? EMPTY_CHOICES.role,
    cpu: initial?.cpu_cores != null ? String(initial.cpu_cores) : "",
    memory: initial?.memory_bytes != null ? String(initial.memory_bytes / GB) : "",
    note: "",
  };
  const [sharing, setSharing] = useState<Sharing | null>(start.sharing);
  const [reach, setReach] = useState<Reach>(start.reach);
  const [subjects, setSubjects] = useState<string[]>(start.subjects);
  const [role, setRole] = useState<Role>(start.role);
  const [cpu, setCpu] = useState(start.cpu);
  const [memory, setMemory] = useState(start.memory);
  const [note, setNote] = useState("");
  const now = { sharing, reach, subjects, role, cpu, memory, note };
  // #779: dirty against what the dialog OPENED with — a form seeded from the
  // view file does not claim unsaved work on sight.
  const attemptClose = useDirtyClose(!sameShape(now, start), onClose);

  const copies = sharing !== null && sharing !== "direct";
  // A9 for agents; for a WUI that hands out copies too, whose copies' banner
  // shows it (docs/plan-wui-copy-update.md D6). ONE rule for showing it and
  // for sending it: a note the dialog no longer shows is not sent.
  const offersNote = !!republish && (kind === "agent" || copies);
  const estimate = useQuery({
    queryKey: qk.publishEstimate(itemId, path),
    queryFn: () => client.estimate(slug, itemId, path),
    enabled: copies,
  });
  const toggle = (subject: string) =>
    setSubjects((s) => (s.includes(subject) ? s.filter((x) => x !== subject) : [...s, subject]));
  const users = subjects.filter((s) => s.startsWith("user:")).map((s) => s.slice(5));
  const cpuValue = cpu.trim() === "" ? null : Number(cpu);
  const memoryValue = memory.trim() === "" ? null : Math.round(Number(memory) * GB);
  const sizeOk =
    (cpuValue === null || (Number.isFinite(cpuValue) && cpuValue > 0)) &&
    (memoryValue === null || (Number.isFinite(memoryValue) && memoryValue > 0));
  const blocked = loadError
    ? "publish.currentFailed.short"
    : sharing === null
      ? "publish.pick"
      : reach === "subjects" && subjects.length === 0 && sharing !== "direct"
        ? "publish.pickPeople"
        : !sizeOk
          ? "publish.size.invalid"
          : null;

  const confirm = () => {
    if (sharing === null || blocked) return;
    onConfirm({
      sharing,
      reach: sharing === "direct" ? "me" : reach,
      subjects: sharing === "direct" || reach !== "subjects" ? [] : subjects,
      role,
      cpu_cores: copies ? cpuValue : null,
      memory_bytes: copies ? memoryValue : null,
      note: offersNote ? note.trim() : "",
    });
  };

  return (
    <ModalShell onClose={attemptClose} labelledBy={titleId} width={560} data-testid="publish-dialog">
      <h2 id={titleId} style={{ margin: "0 0 12px", fontSize: "1.1rem" }}>
        {t(kind === "agent" ? "publish.title.agent" : "publish.title.wui")}
      </h2>

      {loadError && (
        <p role="alert" style={{ ...cost, color: "var(--err)", margin: "0 0 12px" }}>
          {t("publish.currentFailed", { why: loadError })}
        </p>
      )}

      {kind === "wui" && (
        <fieldset style={fieldset}>
          <legend style={legend}>{t("publish.sharing.legend")}</legend>
          {WUI_SHARINGS.map((option) => (
            <label key={option} style={choice}>
              <input
                type="radio"
                name="publish-sharing"
                checked={sharing === option}
                onChange={() => setSharing(option)}
              />
              <span>
                <span style={{ fontWeight: 600 }}>{t(`publish.sharing.${option}` as MsgKey)}</span>
                <span style={cost}>{t(`publish.sharing.${option}.cost` as MsgKey)}</span>
              </span>
            </label>
          ))}
        </fieldset>
      )}

      {sharing === "direct" ? (
        <p style={cost}>{t("publish.reach.direct")}</p>
      ) : (
        <fieldset style={fieldset}>
          <legend style={legend}>{t("publish.reach.legend")}</legend>
          {(["me", "subjects", "everyone"] as const).map((option) => (
            <label key={option} style={choice}>
              <input
                type="radio"
                name="publish-reach"
                checked={reach === option}
                onChange={() => setReach(option)}
              />
              <span>{t(`publish.reach.${option}` as MsgKey)}</span>
            </label>
          ))}
          {reach === "subjects" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
              <span style={cost}>{t("publish.people")}</span>
              <UserPicker
                selected={users}
                onToggle={(id) => toggle(`user:${id}`)}
                // The item's own people first (plan-people-picker-ranking D4).
                related={itemPeople}
              />
              <span style={cost}>{t("publish.groups")}</span>
              {groups.length === 0 ? (
                <span style={cost}>{t("publish.noGroups")}</span>
              ) : (
                groups.map((g) => (
                  <label key={g.resource_id} style={choice}>
                    <input
                      type="checkbox"
                      checked={subjects.includes(`group:${g.resource_id}`)}
                      onChange={() => toggle(`group:${g.resource_id}`)}
                    />
                    <span>{g.name}</span>
                  </label>
                ))
              )}
            </div>
          )}
        </fieldset>
      )}

      {sharing === "shared" && (
        <fieldset style={fieldset}>
          <legend style={legend}>{t("publish.role.legend")}</legend>
          {(["reader", "participant", "collaborator"] as const).map((option) => (
            <label key={option} style={choice}>
              <input
                type="radio"
                name="publish-role"
                checked={role === option}
                onChange={() => setRole(option)}
              />
              <span>{t(`publish.role.${option}` as MsgKey)}</span>
            </label>
          ))}
        </fieldset>
      )}

      {copies && (
        <fieldset style={fieldset}>
          <legend style={legend}>{t("publish.size.legend")}</legend>
          <span style={cost}>{t("publish.size.default")}</span>
          <div style={{ display: "flex", gap: 12, marginTop: 6 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={cost}>{t("publish.size.cpu")}</span>
              <input
                className="input"
                inputMode="decimal"
                autoComplete="off"
                value={cpu}
                onChange={(e) => setCpu(e.target.value)}
                style={{ width: 120 }}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={cost}>{t("publish.size.memory")}</span>
              <input
                className="input"
                inputMode="decimal"
                autoComplete="off"
                value={memory}
                onChange={(e) => setMemory(e.target.value)}
                style={{ width: 120 }}
              />
            </label>
          </div>
        </fieldset>
      )}

      {copies && <EstimateLine estimate={estimate.data} loading={estimate.isPending} />}

      {offersNote && (
        <label style={{ display: "flex", flexDirection: "column", gap: 4, margin: "0 0 12px" }}>
          <span style={legend}>{t("publish.note.legend")}</span>
          <textarea
            className="input"
            rows={3}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
      )}

      <ModalActions>
        {blocked && (
          <span role="status" style={{ ...cost, marginRight: "auto" }}>
            {t(blocked as MsgKey)}
          </span>
        )}
        <button type="button" className="btn" data-size="sm" onClick={attemptClose}>
          {t("publish.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          data-size="sm"
          disabled={blocked !== null}
          onClick={confirm}
        >
          {t("publish.confirm")}
        </button>
      </ModalActions>
    </ModalShell>
  );
}

function EstimateLine({ estimate, loading }: { estimate?: Estimate; loading: boolean }) {
  const t = useT();
  if (loading || estimate === undefined) return <p style={cost}>{t("publish.estimate.loading")}</p>;
  const seconds = Math.max(1, Math.round((estimate.files * MS_PER_FILE) / 1000));
  return (
    <div style={{ margin: "0 0 12px" }}>
      <p style={cost} data-testid="publish-estimate">
        {t("publish.estimate", { files: estimate.files, size: sizeText(estimate.bytes), seconds })}
      </p>
      {estimate.files > MANY_FILES && (
        <div role="note" style={{ ...cost, color: "var(--ink)", fontWeight: 600 }}>
          {t("publish.estimate.many")}
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {estimate.biggest.slice(0, 3).map((f) => (
              <li key={f.folder}>
                {f.folder} · {t("publish.estimate.folder", { files: f.files, size: sizeText(f.bytes) })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const fieldset: React.CSSProperties = {
  border: "none",
  padding: 0,
  margin: "0 0 14px",
  display: "flex",
  flexDirection: "column",
  gap: 8,
};
const legend: React.CSSProperties = { fontWeight: 600, marginBottom: 6, padding: 0 };
const choice: React.CSSProperties = { display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" };
const cost: React.CSSProperties = { display: "block", margin: 0, color: "var(--ink-2)", fontSize: "0.85rem" };

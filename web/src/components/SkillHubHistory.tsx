/**
 * The skill page's 「版本紀錄」 tab (`docs/plan-skill-hub-history.md` §8, laid
 * out by `docs/plan-skill-hub-ux-redo.md` D8, D13, D14).
 *
 * One row per event the server reports, newest first, the latest five until
 * the person asks for all (progressive disclosure). A version — a publish or
 * a rollback — is named `v N ・ date time` everywhere it is referred to (D8):
 * the number is the server's, counted the same for every reader. A transfer
 * or a permission change is a one-line note, not a version. A row shows what
 * changed — the description only when it differs from the version before —
 * and what the review said. Anyone who may read the entry can view, compare
 * and fork a version; only the owner rolls back, and that is a secondary
 * action (Material 3: one primary action per screen). An old version is never
 * installed (G23): fork copies it into a workspace as a starting point.
 *
 * No internals on screen: never a revision id or a commit.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import type {
  SkillHubApi,
  SkillHubDetail,
  SkillHubFileChange,
  SkillHubHistoryEvent,
} from "../api/skillHub";
import { usePickableGroups } from "../hooks/usePickableGroups";
import { useAppItems, useAppManifest, useApps } from "../hooks/useResources";
import { useUsers } from "../hooks/useUsers";
import { ymd } from "../lib/date";
import { useT } from "../lib/i18n";
import { subjectGroup, subjectUser } from "../lib/permission";
import { describeRefusal } from "../lib/skillHubRefusal";
import { skillBody } from "../lib/skillBody";
import { useDialog } from "./Dialog";
import { Icon } from "./Icon";
import { ModalActions } from "./ModalActions";
import { ModalShell } from "./ModalShell";
import { SkillHubFiles } from "./SkillHubFiles";
import { UserChip } from "./UserChip";

/** `YYYY/MM/DD HH:MM`, local time — the same date form as the rest of the app. */
function when(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${ymd(iso)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const namesAVersion = (e: SkillHubHistoryEvent) => e.kind === "publish" || e.kind === "rollback";

/** How many rows show before 「顯示全部」 (D14). */
const FIRST_ROWS = 5;

type Open =
  | { kind: "view"; event: SkillHubHistoryEvent }
  | { kind: "diff"; event: SkillHubHistoryEvent }
  | { kind: "fork"; event: SkillHubHistoryEvent };

export function SkillHubHistory({ entry, client }: { entry: SkillHubDetail; client: SkillHubApi }) {
  const t = useT();
  const qc = useQueryClient();
  const { confirm } = useDialog();
  const users = useUsers();
  const personName = (id: string) => users.find((u) => u.id === id)?.name ?? id;
  const groups = usePickableGroups();
  // A grant by the name people know it by — never the `user:` / `group:` subject.
  const subjectName = (subject: string) => {
    const user = subjectUser(subject);
    if (user !== null) return personName(user);
    const group = subjectGroup(subject);
    if (group !== null) {
      // One the viewer cannot pick is named as a group, not by its id.
      return groups.find((g) => g.resource_id === group)?.name ?? t("skillHub.history.audience.group");
    }
    return t("skillHub.history.audience.everyone"); // `all`, the only other subject
  };
  const { data: events, isPending, isError } = useQuery({
    queryKey: qk.skillHubHistory(entry.id),
    queryFn: () => client.history(entry.id),
  });
  const [open, setOpen] = useState<Open | null>(null);
  const [all, setAll] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const current = events?.find((e) => e.current);
  // The version in force: the newest row that names one. The current ROW may
  // be a transfer or a permission change, which carries the commit but no number.
  const currentVersion = events?.find(namesAVersion);
  // No current row (an empty or still-loading history) means nothing is
  // known to be current: every version reads as "not now".
  const isNow = (e: SkillHubHistoryEvent) => current !== undefined && e.commit === current.commit;
  const byRevision = (revision: string) => events?.find((e) => e.revision === revision);
  /** `v N`, or "" for a row with no number (an entry from before numbering). */
  const vn = (e: SkillHubHistoryEvent | undefined) => (e?.version ? `v${e.version}` : "");
  /** `v N ・ date time` — how every version is referred to (D8). */
  const name = (e: SkillHubHistoryEvent) => (vn(e) ? `${vn(e)} ・ ${when(e.at)}` : when(e.at));
  // Every version once, newest first, as something to compare against.
  const versions = (() => {
    const seen = new Set<string>();
    const out: { revision: string; commit: string; label: string; short: string }[] = [];
    for (const e of events ?? []) {
      if (!namesAVersion(e) || seen.has(e.commit)) continue;
      seen.add(e.commit);
      const isCurrent = e === currentVersion;
      out.push({
        // The current version is compared through the current row: the
        // server's own idea of "now".
        revision: isCurrent && current ? current.revision : e.revision,
        commit: e.commit,
        label: isCurrent ? `${name(e)}（${t("skillHub.history.current")}）` : name(e),
        short: vn(e),
      });
    }
    return out;
  })();
  // The description shows on a version only when it differs from the
  // version before it (D14 「只顯示改了什麼」); the first version's always.
  const previousVersion = (e: SkillHubHistoryEvent) => {
    const list = events ?? [];
    return list.slice(list.indexOf(e) + 1).find(namesAVersion);
  };

  const rollback = useMutation({
    mutationFn: (e: SkillHubHistoryEvent) =>
      client.rollback(entry.id, e.revision, current?.commit ?? ""),
    onMutate: () => setFailure(null),
    onError: (e) => setFailure(describeRefusal(e, t)),
    // Either way the page re-reads: after a rollback to show it, after a
    // refusal because what it showed is no longer what is current.
    onSettled: () => qc.invalidateQueries({ queryKey: ["skillHub"] }),
    meta: { silentError: true },
  });

  const askRollback = async (e: SkillHubHistoryEvent) => {
    const choice = await confirm({
      title: t("skillHub.history.rollback.title", { version: name(e) }),
      body: t("skillHub.history.rollback.body"),
      actions: [
        { id: "cancel", label: t("skillHub.cancel") },
        { id: "rollback", label: t("skillHub.history.rollback.confirm"), variant: "primary" },
      ],
    });
    if (choice === "rollback") rollback.mutate(e);
  };

  const label = (e: SkillHubHistoryEvent) => {
    switch (e.kind) {
      case "publish":
        return t("skillHub.history.kind.publish");
      case "rollback": {
        const back = byRevision(e.to_revision);
        return t("skillHub.history.kind.rollback", {
          version: vn(back) || (back ? when(back.at) : ""),
        });
      }
      case "transfer":
        return t("skillHub.history.kind.transfer", { owner: personName(e.owner) });
      case "permission":
        return t("skillHub.history.kind.permission", {
          visibility:
            e.visibility === "private"
              ? t("skillHub.visibility.private")
              : e.visibility === "restricted"
                ? t("skillHub.visibility.restricted")
                : t("skillHub.visibility.public"),
        });
    }
  };

  const shown = all ? (events ?? []) : (events ?? []).slice(0, FIRST_ROWS);
  return (
    <section>
      {isError ? (
        <p className="error" role="alert">
          {t("skillHub.error")}
        </p>
      ) : isPending || !events ? (
        <p className="muted">{t("skillHub.loading")}</p>
      ) : (
        <>
          <ol className="skill-hub-history" aria-label={t("skillHub.history")}>
            {shown.map((e) => {
              const version = namesAVersion(e);
              const before = version ? previousVersion(e) : undefined;
              return (
                <li
                  key={e.revision}
                  className="skill-hub-history-row"
                  data-kind={version ? "version" : "note"}
                  data-current={(version && e === currentVersion) || undefined}
                >
                  <div className="skill-hub-history-head">
                    {version ? <span className="skill-hub-history-name">{name(e)}</span> : null}
                    <span className="skill-hub-history-kind">{label(e)}</span>
                    {version && e === currentVersion ? (
                      <span className="skill-hub-badge" data-kind="current">
                        {t("skillHub.history.current")}
                      </span>
                    ) : null}
                    <span className="skill-hub-history-meta muted small">
                      <UserChip userId={e.by} size={16} nameOnly />
                      {version ? null : <time dateTime={e.at}>{when(e.at)}</time>}
                    </span>
                  </div>
                  {version && (!before || before.description !== e.description) ? (
                    <p className="skill-hub-history-desc">{e.description}</p>
                  ) : null}
                  {e.kind === "permission" && e.audience.length > 0 ? (
                    // G24: the owner sees who it was opened to, not only the word.
                    <p className="skill-hub-history-desc">
                      {t("skillHub.history.audience", { who: e.audience.map(subjectName).join(", ") })}
                    </p>
                  ) : null}
                  {version && e.review_notes.length > 0 ? (
                    // What the review said about this version (§8) — the one a
                    // rollback brings back is not reviewed again (G20).
                    <ul className="skill-hub-history-notes" aria-label={t("skillHub.review")}>
                      {e.review_notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  ) : null}
                  {version ? (
                    <div className="skill-hub-history-actions">
                      {e.current ? null : (
                        <button
                          type="button"
                          className="btn"
                          data-size="sm"
                          data-variant="secondary"
                          onClick={() => setOpen({ kind: "view", event: e })}
                        >
                          {t("skillHub.history.view")}
                        </button>
                      )}
                      {/* A row whose version IS the current one has nothing
                          to compare and nothing to roll back to. */}
                      {isNow(e) ? null : (
                        <button
                          type="button"
                          className="btn"
                          data-size="sm"
                          data-variant="secondary"
                          onClick={() => setOpen({ kind: "diff", event: e })}
                        >
                          {t("skillHub.history.compare")}
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn"
                        data-size="sm"
                        data-variant="secondary"
                        onClick={() => setOpen({ kind: "fork", event: e })}
                      >
                        {t("skillHub.history.fork")}
                      </button>
                      {entry.is_owner && !isNow(e) ? (
                        <button
                          type="button"
                          className="btn"
                          data-size="sm"
                          data-variant="secondary"
                          disabled={rollback.isPending}
                          onClick={() => void askRollback(e)}
                        >
                          {t("skillHub.history.rollback")}
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
          {!all && events.length > FIRST_ROWS ? (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              onClick={() => setAll(true)}
            >
              {t("skillHub.history.showAll", { count: events.length })}
            </button>
          ) : null}
        </>
      )}
      {failure ? (
        <p className="error" role="alert">
          {t("skillHub.failed", { reason: failure })}
        </p>
      ) : null}

      {open?.kind === "view" ? (
        <VersionModal
          entryId={entry.id}
          event={open.event}
          title={name(open.event)}
          client={client}
          onClose={() => setOpen(null)}
        />
      ) : null}
      {open?.kind === "diff" && current ? (
        <DiffModal
          entryId={entry.id}
          from={open.event}
          fromName={vn(open.event) || when(open.event.at)}
          others={versions.filter((v) => v.commit !== open.event.commit)}
          initial={current.revision}
          client={client}
          onClose={() => setOpen(null)}
        />
      ) : null}
      {open?.kind === "fork" ? (
        <ForkDialog
          entry={entry}
          event={open.event}
          day={name(open.event)}
          client={client}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </section>
  );
}

/** One version, read: its SKILL.md, and its files the way the files tab
 * shows the current ones (D12). */
function VersionModal({
  entryId,
  event,
  title,
  client,
  onClose,
}: {
  entryId: string;
  event: SkillHubHistoryEvent;
  title: string;
  client: SkillHubApi;
  onClose: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const version = useQuery({
    queryKey: qk.skillHubVersion(entryId, event.revision),
    queryFn: () => client.version(entryId, event.revision),
  });
  return (
    // A read-only viewer: nothing to lose, so a stray click closes it (#779).
    <ModalShell onClose={onClose} labelledBy={titleId} width={860} closeOnBackdrop data-testid="skill-hub-version">
      <h2 id={titleId} className="modal-title">
        {title}
      </h2>
      {version.isError ? (
        <p className="error" role="alert">
          {t("skillHub.error")}
        </p>
      ) : !version.data ? (
        <p className="muted">{t("skillHub.loading")}</p>
      ) : (
        <>
          <div className="skill-hub-md markdown">
            <ReactMarkdown>{skillBody(version.data.skill_md)}</ReactMarkdown>
          </div>
          <h3>{t("skillHub.files")}</h3>
          <SkillHubFiles
            entryId={entryId}
            revision={event.revision}
            files={version.data.files}
            scripts={version.data.scripts}
            client={client}
          />
        </>
      )}
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.history.close")}
        </button>
      </ModalActions>
    </ModalShell>
  );
}

/** One version against another (GitHub's "Files changed"): how many files
 * were added, changed, removed; then each file on request, its lines in red
 * and green without the raw `---`/`+++`/`@@` headers (D13). Against the
 * current version unless the viewer picks another (§8 「能和另一版比對」). */
function DiffModal({
  entryId,
  from,
  fromName,
  others,
  initial,
  client,
  onClose,
}: {
  entryId: string;
  from: SkillHubHistoryEvent;
  fromName: string;
  /** The versions it can be compared with — never itself. */
  others: { revision: string; label: string; short: string }[];
  initial: string;
  client: SkillHubApi;
  onClose: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const againstId = useId();
  const [to, setTo] = useState(initial);
  const diff = useQuery({
    queryKey: qk.skillHubDiff(entryId, from.revision, to),
    queryFn: () => client.diff(entryId, from.revision, to),
  });
  const toName = others.find((v) => v.revision === to)?.short ?? "";
  const count = (status: SkillHubFileChange["status"]) =>
    (diff.data ?? []).filter((f) => f.status === status).length;
  return (
    // Read-only: a stray click closes it (#779).
    <ModalShell onClose={onClose} labelledBy={titleId} width={860} closeOnBackdrop data-testid="skill-hub-diff">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.history.diff.title", { from: fromName, to: toName })}
      </h2>
      <label className="skill-hub-diff-against" htmlFor={againstId}>
        <span>{t("skillHub.history.diff.against")}</span>
        <select id={againstId} className="input" value={to} onChange={(e) => setTo(e.target.value)}>
          {others.map((v) => (
            <option key={v.revision} value={v.revision}>
              {v.label}
            </option>
          ))}
        </select>
      </label>
      {diff.isError ? (
        <p className="error" role="alert">
          {t("skillHub.error")}
        </p>
      ) : !diff.data ? (
        <p className="muted">{t("skillHub.loading")}</p>
      ) : diff.data.length === 0 ? (
        <p className="muted">{t("skillHub.history.noChanges")}</p>
      ) : (
        <>
          <p className="skill-hub-diff-summary">
            {t("skillHub.history.diff.summary", {
              added: count("added"),
              changed: count("changed"),
              removed: count("removed"),
            })}
          </p>
          <ul className="skill-hub-diff">
            {diff.data.map((f) => (
              <DiffFile key={f.path} file={f} />
            ))}
          </ul>
        </>
      )}
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.history.close")}
        </button>
      </ModalActions>
    </ModalShell>
  );
}

const STATUS = {
  added: "skillHub.history.status.added",
  removed: "skillHub.history.status.removed",
  changed: "skillHub.history.status.changed",
} as const;

/** One file of a comparison, closed until asked for. */
function DiffFile({ file }: { file: SkillHubFileChange }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button
        type="button"
        className="skill-hub-diff-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Icon name={open ? "chev_d" : "chev_r"} size={12} />
        <code>{file.path}</code>
        <span className="skill-hub-badge" data-kind={file.status}>
          {t(STATUS[file.status])}
        </span>
      </button>
      {open ? (
        file.patch === null ? (
          <p className="muted small">{t("skillHub.history.binaryChanged")}</p>
        ) : (
          <PatchLines patch={file.patch} />
        )
      ) : null}
    </li>
  );
}

/** A unified diff's lines: the file header (everything before the first
 * hunk — the server's `--- a/` / `+++ b/` pair) goes, a hunk header becomes a
 * gap, and each line is marked `+` / `−` in its own column as well as
 * coloured — colour alone is not a signal (WCAG 1.4.1). Only the lines before
 * the first hunk are dropped: a removed line reading `-- x` is `--- x` in the
 * patch, and is content. */
function PatchLines({ patch }: { patch: string }) {
  const all = patch.split("\n");
  if (all[all.length - 1] === "") all.pop();
  const first = all.findIndex((l) => l.startsWith("@@"));
  const lines = first < 0 ? all : all.slice(first);
  return (
    <div className="skill-hub-patch">
      {lines.map((line, i) => {
        // git's "\ No newline at end of file" is about the line above, not a line.
        if (line.startsWith("\\")) return null;
        if (line.startsWith("@@")) {
          // The first hunk needs no gap above it.
          return i === 0 ? null : (
            <div key={i} className="skill-hub-patch-gap" data-line="gap" aria-hidden="true">
              ⋯
            </div>
          );
        }
        const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx";
        return (
          <div key={i} className="skill-hub-patch-line" data-line={kind}>
            <span className="skill-hub-patch-mark" aria-hidden="true">
              {kind === "add" ? "+" : kind === "del" ? "−" : " "}
            </span>
            <span>{line.slice(1)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 〔從這一版 fork〕: pick an App, then one of its items; the version is
 * copied there. A pick is cheap to redo, so the dialog does not guard its
 * close (#779: a guard that fires over nothing worth keeping trains people
 * to click through it); it is still a form, so no backdrop close. */
function ForkDialog({
  entry,
  event,
  day,
  client,
  onClose,
}: {
  entry: SkillHubDetail;
  event: SkillHubHistoryEvent;
  day: string;
  client: SkillHubApi;
  onClose: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const apps = useApps();
  const [slug, setSlug] = useState(entry.source_app);
  const manifest = useAppManifest(slug);
  const { items, isPending } = useAppItems(slug, manifest?.resource_route);
  const [itemId, setItemId] = useState<string | null>(null);
  const qc = useQueryClient();
  const fork = useMutation({
    mutationFn: () => client.fork(slug, itemId as string, entry.id, event.revision),
    // The item now holds the copy: its Skills list (and any chat card reading
    // it) re-reads, as after an install.
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId as string) }),
    meta: { silentError: true },
  });
  const done = fork.data;
  return (
    <ModalShell onClose={onClose} labelledBy={titleId} width={520} data-testid="skill-hub-fork">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.history.fork.title", { when: day })}
      </h2>
      <p className="hint">{t("skillHub.history.fork.body")}</p>
      {done ? (
        <p role="status">
          {t("skillHub.history.fork.done", { name: done.name })}{" "}
          <Link to={`/a/${encodeURIComponent(slug)}/${encodeURIComponent(itemId as string)}`}>
            {t("skillHub.history.fork.open")}
          </Link>
        </p>
      ) : (
        <>
          <label className="skill-hub-fork-app">
            <span>{t("skillHub.history.fork.app")}</span>
            <select
              className="input"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setItemId(null);
              }}
            >
              {(apps.some((a) => a.slug === slug) ? apps : [{ slug, title: slug }, ...apps]).map((a) => (
                <option key={a.slug} value={a.slug}>
                  {a.title || a.slug}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="skill-hub-fork-items">
            <legend>{t("skillHub.history.fork.items")}</legend>
            {isPending && manifest ? (
              <p className="muted">{t("skillHub.loading")}</p>
            ) : items.length === 0 ? (
              <p className="muted">{t("skillHub.history.fork.noItems")}</p>
            ) : (
              items.map((it) => (
                <label key={it.resource_id} className="skill-hub-fork-item">
                  <input
                    type="radio"
                    name={`${titleId}-item`}
                    checked={itemId === it.resource_id}
                    onChange={() => setItemId(it.resource_id)}
                  />
                  <span>{it.title || it.resource_id}</span>
                </label>
              ))
            )}
          </fieldset>
          {fork.isError ? (
            <p className="error" role="alert">
              {t("skillHub.failed", { reason: describeRefusal(fork.error, t) })}
            </p>
          ) : null}
        </>
      )}
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {done ? t("skillHub.history.close") : t("skillHub.cancel")}
        </button>
        {done ? null : (
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={!itemId || fork.isPending}
            onClick={() => fork.mutate()}
          >
            {t("skillHub.history.fork.confirm")}
          </button>
        )}
      </ModalActions>
    </ModalShell>
  );
}

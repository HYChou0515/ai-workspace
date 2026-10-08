/**
 * The skill hub detail page's 「版本紀錄」 (`docs/plan-skill-hub-history.md` §8).
 *
 * One row per event the server reports, newest first: a publish, a rollback,
 * a transfer, and — for the owner alone, the server's call — a visibility
 * change. A row that names a version (publish / rollback) can be read,
 * compared with any other version and forked by anyone who may read the entry;
 * only the owner can roll back to it. An old version is never installed
 * (G23): 〔從這一版 fork〕 copies it into an item as a starting point of the
 * viewer's own, which is never offered the entry's newer versions.
 *
 * No internals on screen: a version is named by when it was published, never
 * by its id or commit.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";

import { qk } from "../api/queryKeys";
import type { SkillHubApi, SkillHubDetail, SkillHubHistoryEvent } from "../api/skillHub";
import { usePickableGroups } from "../hooks/usePickableGroups";
import { useAppItems, useAppManifest, useApps } from "../hooks/useResources";
import { useUsers } from "../hooks/useUsers";
import { ymd } from "../lib/date";
import { useT } from "../lib/i18n";
import { subjectGroup, subjectUser } from "../lib/permission";
import { describeRefusal } from "../lib/skillHubRefusal";
import { skillBody } from "../lib/skillBody";
import { useDialog } from "./Dialog";
import { ModalActions } from "./ModalActions";
import { ModalShell } from "./ModalShell";
import { UserChip } from "./UserChip";

/** `YYYY/MM/DD HH:MM`, local time — the same date form as the rest of the app. */
function when(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${ymd(iso)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const namesAVersion = (e: SkillHubHistoryEvent) => e.kind === "publish" || e.kind === "rollback";

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
  const [failure, setFailure] = useState<string | null>(null);
  const current = events?.find((e) => e.current);
  // No current row (an empty or still-loading history) means nothing is
  // known to be current: every version reads as "not now".
  const isNow = (e: SkillHubHistoryEvent) => current !== undefined && e.commit === current.commit;
  // A rollback names the version it brought back by when that version was
  // first published — the row the server points at with `to_revision`.
  const publishedAt = (revision: string) => {
    const at = events?.find((e) => e.revision === revision)?.at;
    return at ? ymd(at) : "";
  };
  const versionDay = (e: SkillHubHistoryEvent) =>
    ymd(e.kind === "rollback" ? (events?.find((x) => x.revision === e.to_revision)?.at ?? e.at) : e.at);
  // Every version once, newest first, as something to compare against: the
  // current one through the current row, any other through its newest row.
  // Named by the day it was first published (the oldest row with its commit).
  const versions = (() => {
    const seen = new Set<string>();
    const out: { revision: string; commit: string; label: string }[] = [];
    for (const e of events ?? []) {
      if (seen.has(e.commit) || !(namesAVersion(e) || e.current)) continue;
      seen.add(e.commit);
      const isCurrent = current !== undefined && e.commit === current.commit;
      const first = [...(events ?? [])].reverse().find((x) => x.commit === e.commit && namesAVersion(x));
      const day = ymd(first?.at ?? e.at);
      out.push({
        revision: isCurrent && current ? current.revision : e.revision,
        commit: e.commit,
        label: isCurrent ? `${day} · ${t("skillHub.history.current")}` : day,
      });
    }
    return out;
  })();

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
    const day = versionDay(e);
    const choice = await confirm({
      title: t("skillHub.history.rollback.title", { when: day }),
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
      case "rollback":
        return t("skillHub.history.kind.rollback", { when: publishedAt(e.to_revision) });
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

  return (
    <section>
      <h2>{t("skillHub.history")}</h2>
      {isError ? (
        <p className="error" role="alert">
          {t("skillHub.error")}
        </p>
      ) : isPending || !events ? (
        <p className="muted">{t("skillHub.loading")}</p>
      ) : (
        <ol className="skill-hub-history" aria-label={t("skillHub.history")}>
          {events.map((e) => (
            <li key={e.revision} className="skill-hub-history-row" data-current={e.current || undefined}>
              <div className="skill-hub-history-head">
                <span className="skill-hub-history-kind">{label(e)}</span>
                {e.current ? (
                  <span className="skill-hub-badge" data-kind="current">
                    {t("skillHub.history.current")}
                  </span>
                ) : null}
              </div>
              <div className="skill-hub-history-meta muted small">
                <UserChip userId={e.by} size={16} />
                <time dateTime={e.at}>{when(e.at)}</time>
              </div>
              {namesAVersion(e) ? (
                <p className="skill-hub-history-desc">{e.description}</p>
              ) : null}
              {e.kind === "permission" && e.audience.length > 0 ? (
                // G24: the owner sees who it was opened to, not only the word.
                <p className="skill-hub-history-desc">
                  {t("skillHub.history.audience", { who: e.audience.map(subjectName).join(", ") })}
                </p>
              ) : null}
              {namesAVersion(e) && e.review_notes.length > 0 ? (
                // What the review said about this version (§8) — the one a
                // rollback brings back is not reviewed again (G20).
                <ul className="skill-hub-history-notes" aria-label={t("skillHub.review")}>
                  {e.review_notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              ) : null}
              {namesAVersion(e) ? (
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
                  {/* A row whose version IS the current one (the current row,
                      or the publish a rollback brought back) has nothing to
                      compare and nothing to roll back to. */}
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
                      data-variant="primary"
                      disabled={rollback.isPending}
                      onClick={() => void askRollback(e)}
                    >
                      {t("skillHub.history.rollback")}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ol>
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
          day={versionDay(open.event)}
          client={client}
          onClose={() => setOpen(null)}
        />
      ) : null}
      {open?.kind === "diff" && current ? (
        <DiffModal
          entryId={entry.id}
          from={open.event}
          others={versions.filter((v) => v.commit !== open.event.commit)}
          initial={current.revision}
          day={versionDay(open.event)}
          client={client}
          onClose={() => setOpen(null)}
        />
      ) : null}
      {open?.kind === "fork" ? (
        <ForkDialog
          entry={entry}
          event={open.event}
          day={versionDay(open.event)}
          client={client}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </section>
  );
}

/** One version, read: its SKILL.md and its files, one file's text on demand. */
function VersionModal({
  entryId,
  event,
  day,
  client,
  onClose,
}: {
  entryId: string;
  event: SkillHubHistoryEvent;
  day: string;
  client: SkillHubApi;
  onClose: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const [path, setPath] = useState<string | null>(null);
  const version = useQuery({
    queryKey: qk.skillHubVersion(entryId, event.revision),
    queryFn: () => client.version(entryId, event.revision),
  });
  const file = useQuery({
    queryKey: qk.skillHubVersionFile(entryId, event.revision, path ?? ""),
    queryFn: () => client.versionFile(entryId, event.revision, path as string),
    enabled: path !== null,
  });
  return (
    // A read-only viewer: nothing to lose, so a stray click closes it (#779).
    <ModalShell onClose={onClose} labelledBy={titleId} width={720} closeOnBackdrop data-testid="skill-hub-version">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.history.version.title", { when: day })}
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
          <ul className="skill-hub-files">
            {version.data.files.map(({ path: f }) => (
              <li key={f}>
                <button
                  type="button"
                  className="btn"
                  data-size="sm"
                  data-variant="ghost"
                  aria-pressed={path === f}
                  onClick={() => setPath(f)}
                >
                  {f}
                </button>
              </li>
            ))}
          </ul>
          {path !== null && file.data ? (
            file.data.text === null ? (
              <p className="muted">{t("skillHub.history.notText")}</p>
            ) : (
              <pre className="skill-hub-pre">{file.data.text}</pre>
            )
          ) : null}
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

/** One version against another, per file — the current one unless the
 * viewer picks a different one (§8 「能和另一版比對」). */
function DiffModal({
  entryId,
  from,
  others,
  initial,
  day,
  client,
  onClose,
}: {
  entryId: string;
  from: SkillHubHistoryEvent;
  /** The versions it can be compared with — never itself. */
  others: { revision: string; label: string }[];
  initial: string;
  day: string;
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
  const status = { added: "skillHub.history.status.added", removed: "skillHub.history.status.removed", changed: "skillHub.history.status.changed" } as const;
  return (
    // Read-only: a stray click closes it (#779).
    <ModalShell onClose={onClose} labelledBy={titleId} width={760} closeOnBackdrop data-testid="skill-hub-diff">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.history.diff.title", { when: day })}
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
        <ul className="skill-hub-diff">
          {diff.data.map((f) => (
            <li key={f.path}>
              <div className="skill-hub-diff-head">
                <code>{f.path}</code>
                <span className="skill-hub-badge" data-kind={f.status}>
                  {t(status[f.status])}
                </span>
              </div>
              {f.patch === null ? (
                <p className="muted small">{t("skillHub.history.binaryChanged")}</p>
              ) : (
                <pre className="skill-hub-pre">{f.patch}</pre>
              )}
            </li>
          ))}
        </ul>
      )}
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.history.close")}
        </button>
      </ModalActions>
    </ModalShell>
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

/**
 * `/skill-hub/:entryId` — one published skill (`docs/plan-skill-hub.md`).
 *
 * Everyone who may read it sees the same page: the SKILL.md, who published
 * it and from which App, the tools it mentions, the AI review's notes, what
 * it was forked from (and whether that original is still up), and its own
 * forks. What differs is the ACTIONS: the owner's five — Edit, Unpublish /
 * Republish, Visibility, Transfer, Delete — and nobody else's. Not one
 * button for a non-owner (plan Q7): installing is done in an item's Skills
 * panel (D2), which the page says in a sentence rather than a control.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useNavigate, useParams } from "react-router-dom";

import { qk } from "../api/queryKeys";
import {
  type SkillEditTarget,
  type SkillHubApi,
  type SkillHubDetail,
  skillHubApi,
} from "../api/skillHub";
import { AppTag } from "../components/AppTag";
import { useDialog } from "../components/Dialog";
import { ModalActions } from "../components/ModalActions";
import { ModalShell } from "../components/ModalShell";
import { PermissionDialog } from "../components/PermissionDialog";
import { UserChip } from "../components/UserChip";
import { UserPicker } from "../components/UserPicker";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { usePickableGroups } from "../hooks/usePickableGroups";
import { useApps } from "../hooks/useResources";
import { useT } from "../lib/i18n";
import { DOC_ROLES } from "../lib/permission";

export function SkillHubEntryPage({ client = skillHubApi }: { client?: SkillHubApi }) {
  const { entryId = "" } = useParams();
  const t = useT();
  const { data, isPending, isError, refetch } = useQuery({
    queryKey: qk.skillHubEntry(entryId, ""),
    queryFn: () => client.get(entryId),
    enabled: entryId !== "",
  });
  useBreadcrumbs([
    { label: t("nav.home"), to: "/" },
    { label: "Skill hub", to: "/skill-hub" },
    { label: data ? `${data.owner}/${data.name}` : "…" },
  ]);

  if (isError) {
    return (
      <div className="page">
        <h1>Skill hub</h1>
        <p className="error" role="alert">
          {t("skillHub.error")}{" "}
          <button
            type="button"
            className="btn"
            data-variant="secondary"
            data-size="sm"
            onClick={() => void refetch()}
          >
            {t("skillHub.retry")}
          </button>
        </p>
      </div>
    );
  }
  if (isPending || !data) return <p>{t("skillHub.loading")}</p>;
  return <EntryView entry={data} client={client} />;
}

function EntryView({ entry, client }: { entry: SkillHubDetail; client: SkillHubApi }) {
  const t = useT();
  const apps = useApps();
  const appTitle = (slug: string) => apps.find((a) => a.slug === slug)?.title || slug;
  return (
    <div className="page skill-hub-entry">
      <div className="page-head">
        <h1>
          <span className="skill-hub-owner">{entry.owner}/</span>
          {entry.name}
        </h1>
        {entry.is_owner ? <OwnerActions entry={entry} client={client} /> : null}
      </div>
      <p className="skill-hub-entry-desc">{entry.description}</p>
      <div className="skill-hub-entry-meta">
        <UserChip userId={entry.owner} size={20} />
        <AppTag slug={entry.source_app} />
        <span className="muted">{t("skillHub.writtenIn", { app: appTitle(entry.source_app) })}</span>
        {entry.is_owner ? (
          <span className="skill-hub-badge" data-kind={entry.visibility}>
            {entry.visibility === "private"
              ? t("skillHub.visibility.private")
              : entry.visibility === "restricted"
                ? t("skillHub.visibility.restricted")
                : t("skillHub.visibility.public")}
          </span>
        ) : null}
      </div>
      {entry.forked_from ? <Lineage lineage={entry.forked_from} /> : null}
      {entry.is_owner ? null : <p className="hint">{t("skillHub.howToInstall")}</p>}

      <section>
        <h2>{t("skillHub.tools")}</h2>
        {entry.referenced_tools.length === 0 ? (
          <p className="muted">{t("skillHub.tools.none")}</p>
        ) : (
          <ul className="skill-hub-chips">
            {entry.referenced_tools.map((tool) => (
              <li key={tool}>
                <code>{tool}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>{t("skillHub.review")}</h2>
        {entry.review.notes.length === 0 ? (
          <p className="muted">{t("skillHub.review.ok")}</p>
        ) : (
          <ul className="skill-hub-notes">
            {entry.review.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
        {entry.review.model ? (
          <p className="muted small">{t("skillHub.review.by", { model: entry.review.model })}</p>
        ) : null}
      </section>

      <section>
        <h2>SKILL.md</h2>
        <div className="skill-hub-md markdown">
          <ReactMarkdown>{skillBody(entry.skill_md)}</ReactMarkdown>
        </div>
      </section>

      <section>
        <h2>{t("skillHub.files")}</h2>
        <ul className="skill-hub-files">
          {entry.files.map((f) => (
            <li key={f}>
              <code>{f}</code>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>{t("skillHub.forksOf")}</h2>
        {entry.forks.length === 0 ? (
          <p className="muted">{t("skillHub.forksOf.none")}</p>
        ) : (
          <ul className="skill-hub-list">
            {entry.forks.map((f) => (
              <li key={f.id} className="skill-hub-row" data-fork>
                <div className="skill-hub-row-head">
                  <Link to={`/skill-hub/${encodeURIComponent(f.id)}`} className="skill-hub-row-title">
                    <span className="skill-hub-owner">{f.owner}/</span>
                    {f.name}
                  </Link>
                  <AppTag slug={f.source_app} />
                </div>
                <p className="skill-hub-row-desc">{f.description}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** The SKILL.md without its frontmatter. The name and description are already
 * on the page, and rendered as Markdown a `---` fence under a line of text
 * reads as a setext heading — measured: "name: triage-reflow" drawn as an h1
 * over the real one. Display only; the file itself ships whole. */
export function skillBody(md: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(md);
  return m ? md.slice(m[0].length).replace(/^\s+/, "") : md;
}

/** What this entry was forked from, as the viewer may know it. A root that
 * went private or was deleted is SAID — the plan's 「原作已下架 / 已刪除」 —
 * never a broken link. */
function Lineage({ lineage }: { lineage: NonNullable<SkillHubDetail["forked_from"]> }) {
  const t = useT();
  if (lineage.state === "live") {
    return (
      <p className="skill-hub-lineage">
        <Link to={`/skill-hub/${encodeURIComponent(lineage.entry)}`}>
          {t("skillHub.forkOf", { origin: `${lineage.owner}/${lineage.name}` })}
        </Link>
      </p>
    );
  }
  return (
    <p className="skill-hub-lineage muted">
      {lineage.state === "unpublished"
        ? t("skillHub.origin.unpublished")
        : t("skillHub.origin.deleted")}
    </p>
  );
}

/** The owner's five actions. Rendered for the owner ONLY — the caller gates
 * on `entry.is_owner`, the server's answer, and every one of these routes
 * refuses a non-owner anyway. */
function OwnerActions({ entry, client }: { entry: SkillHubDetail; client: SkillHubApi }) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm } = useDialog();
  const pickableGroups = usePickableGroups();
  const [sharing, setSharing] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [newItem, setNewItem] = useState<SkillEditTarget | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["skillHub"] });
  const failed = (e: unknown) => setFailure(e instanceof Error ? e.message : String(e));

  const unpublish = useMutation({
    mutationFn: () => (entry.visibility === "private" ? client.republish(entry.id) : client.unpublish(entry.id)),
    onSuccess: refresh,
    onError: failed,
  });
  const permission = useMutation({
    mutationFn: (perm: Parameters<SkillHubApi["setPermission"]>[1]) =>
      client.setPermission(entry.id, perm),
    onSuccess: () => {
      setSharing(false);
      void refresh();
    },
    onError: failed,
  });
  const transfer = useMutation({
    mutationFn: (owner: string) => client.transfer(entry.id, owner),
    onSuccess: () => {
      setTransferring(false);
      void refresh();
    },
    onError: failed,
  });
  const remove = useMutation({
    mutationFn: () => client.remove(entry.id),
    onSuccess: () => {
      void refresh();
      navigate("/skill-hub", { replace: true });
    },
    onError: failed,
  });
  const edit = useMutation({
    mutationFn: () => client.edit(entry.id),
    onSuccess: (target) => {
      if (target.action === "open") {
        navigate(`/a/${encodeURIComponent(target.app)}/${encodeURIComponent(target.item_id)}`);
      } else {
        setNewItem(target);
      }
    },
    onError: failed,
  });

  const askDelete = async () => {
    const choice = await confirm({
      title: t("skillHub.delete.title", { name: entry.name }),
      body: t("skillHub.delete.body"),
      actions: [
        { id: "cancel", label: t("skillHub.cancel") },
        { id: "delete", label: t("skillHub.delete.confirm"), variant: "danger" },
      ],
    });
    if (choice === "delete") remove.mutate();
  };
  const busy =
    unpublish.isPending || remove.isPending || edit.isPending || transfer.isPending;

  return (
    <div className="skill-hub-actions" role="group" aria-label={t("skillHub.edit")}>
      <button type="button" className="btn" data-size="sm" data-variant="primary" disabled={busy} onClick={() => edit.mutate()}>
        {t("skillHub.edit")}
      </button>
      <button type="button" className="btn" data-size="sm" data-variant="secondary" disabled={busy} onClick={() => unpublish.mutate()}>
        {entry.visibility === "private" ? t("skillHub.republish") : t("skillHub.unpublish")}
      </button>
      <button type="button" className="btn" data-size="sm" data-variant="secondary" disabled={busy} onClick={() => setSharing(true)}>
        {t("skillHub.share")}
      </button>
      <button type="button" className="btn" data-size="sm" data-variant="secondary" disabled={busy} onClick={() => setTransferring(true)}>
        {t("skillHub.transfer")}
      </button>
      <button type="button" className="btn" data-size="sm" data-variant="danger" disabled={busy} onClick={() => void askDelete()}>
        {t("skillHub.delete")}
      </button>
      {failure ? (
        <p className="error" role="alert">
          {t("skillHub.failed", { reason: failure })}
        </p>
      ) : null}

      {sharing && entry.permission ? (
        <PermissionDialog
          resourceName={`${entry.owner}/${entry.name}`}
          owner={entry.owner}
          value={entry.permission}
          roles={DOC_ROLES}
          caption={t("skillHub.share.caption")}
          pickableGroups={pickableGroups}
          busy={permission.isPending}
          onSubmit={(perm) => permission.mutate(perm)}
          onClose={() => setSharing(false)}
        />
      ) : null}

      {transferring ? (
        <TransferDialog
          name={entry.name}
          owner={entry.owner}
          busy={transfer.isPending}
          onSubmit={(owner) => transfer.mutate(owner)}
          onClose={() => setTransferring(false)}
        />
      ) : null}

      {newItem ? (
        <NewItemDialog target={newItem} onClose={() => setNewItem(null)} />
      ) : null}
    </div>
  );
}

/** Pick the new owner. One person, then confirm — the picker is the
 * platform's, so the search is over the same directory every share UI uses. */
function TransferDialog({
  name,
  owner,
  busy,
  onSubmit,
  onClose,
}: {
  name: string;
  owner: string;
  busy: boolean;
  onSubmit: (owner: string) => void;
  onClose: () => void;
}) {
  const t = useT();
  const titleId = useId();
  const [picked, setPicked] = useState<string | null>(null);
  return (
    <ModalShell onClose={onClose} labelledBy={titleId} width={480} data-testid="skill-hub-transfer">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.transfer.title", { name })}
      </h2>
      <p className="hint">{t("skillHub.transfer.body")}</p>
      <UserPicker
        selected={picked ? [picked] : []}
        onToggle={(id) => setPicked((cur) => (cur === id ? null : id))}
        exclude={[owner]}
      />
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          disabled={!picked || busy}
          onClick={() => picked && onSubmit(picked)}
        >
          {t("skillHub.transfer.confirm")}
        </button>
      </ModalActions>
    </ModalShell>
  );
}

/** The edit resolver's `new_item` branch: say why the source item cannot
 * take the edit, and what to do instead. */
function NewItemDialog({ target, onClose }: { target: SkillEditTarget; onClose: () => void }) {
  const t = useT();
  const apps = useApps();
  const appTitle = apps.find((a) => a.slug === target.app)?.title || target.app;
  const reasonKey =
    target.reason === "closed"
      ? "skillHub.edit.reason.closed"
      : target.reason === "deleted"
        ? "skillHub.edit.reason.deleted"
        : "skillHub.edit.reason.no_access";
  const titleId = useId();
  return (
    // A read-only explanation: nothing to lose, so a stray click closes it (#779).
    <ModalShell onClose={onClose} labelledBy={titleId} width={480} closeOnBackdrop data-testid="skill-hub-new-item">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.edit.newItem.title")}
      </h2>
      <p>{t(reasonKey)}</p>
      <p className="hint">{t("skillHub.edit.newItem.how", { app: appTitle })}</p>
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.cancel")}
        </button>
        <Link className="btn" data-variant="primary" to={`/a/${encodeURIComponent(target.app)}/new`}>
          {t("skillHub.edit.newItem.go")}
        </Link>
      </ModalActions>
    </ModalShell>
  );
}

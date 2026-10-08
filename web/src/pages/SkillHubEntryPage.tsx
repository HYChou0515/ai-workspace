/**
 * `/skill-hub/:entryId` — one published skill (`docs/plan-skill-hub.md`,
 * laid out by `docs/plan-skill-hub-ux-redo.md` D5).
 *
 * A header (name, description, owner, what it was forked from — and the
 * owner's 「管理 ▾」), tabs kept in the address (`?tab=`: 說明 / 檔案 / 版本紀錄
 * / fork — VS Code Marketplace's Details / Changelog, npm's Readme / Code /
 * Versions), and a sidebar with what a person decides by: 「安裝到
 * workspace…」, where they already have it, the permissions (owner), when it
 * changed, the counts, the source App, the tools it mentions and the AI
 * review. Installing happens here (D6) and in a workspace's Skills panel.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { HttpError } from "../api/http";
import { qk } from "../api/queryKeys";
import { invalidateHubInstalls } from "../api/skillHubCache";
import {
  type SkillEditTarget,
  type SkillHubApi,
  type SkillHubDetail,
  skillHubApi,
} from "../api/skillHub";
import { ActionMenu } from "../components/ActionMenu";
import { useDialog } from "../components/Dialog";
import { ModalActions } from "../components/ModalActions";
import { ModalShell } from "../components/ModalShell";
import { PageNotice, type PageNoticeContent } from "../components/PageNotice";
import { PermissionDialog } from "../components/PermissionDialog";
import { SkillHubFiles } from "../components/SkillHubFiles";
import { SkillHubHistory } from "../components/SkillHubHistory";
import { ForkOf, SkillHubRow } from "../components/SkillHubRow";
import { UserChip } from "../components/UserChip";
import { UserPicker } from "../components/UserPicker";
import { useBreadcrumbs } from "../hooks/breadcrumbs";
import { usePickableGroups } from "../hooks/usePickableGroups";
import { useApps } from "../hooks/useResources";
import { useUsers } from "../hooks/useUsers";
import { ymd } from "../lib/date";
import { useT } from "../lib/i18n";
import { skillBodyUnderTitle } from "../lib/skillBody";
import { countsText } from "../lib/skillHubCounts";
import { describeRefusal } from "../lib/skillHubRefusal";
import { DOC_ROLES } from "../lib/permission";

const TABS = ["readme", "files", "history", "forks"] as const;

/** The SKILL.md's headings, one level under the page's: its `#` is not the
 * page's title, and a second h1 told assistive tech the page had two. */
const UNDER_THE_TITLE = {
  h1: ({ children }: { children?: React.ReactNode }) => <h2>{children}</h2>,
  h2: ({ children }: { children?: React.ReactNode }) => <h3>{children}</h3>,
  h3: ({ children }: { children?: React.ReactNode }) => <h4>{children}</h4>,
  h4: ({ children }: { children?: React.ReactNode }) => <h5>{children}</h5>,
  h5: ({ children }: { children?: React.ReactNode }) => <h6>{children}</h6>,
};
type Tab = (typeof TABS)[number];

/** Gone — deleted, unpublished, never there, or not the viewer's to read: the
 * server answers all of them alike (Q10), and none is fixed by trying again. */
function isGone(e: unknown): boolean {
  return e instanceof HttpError && (e.status === 404 || e.code === "not_found");
}

export function SkillHubEntryPage({ client = skillHubApi }: { client?: SkillHubApi }) {
  const { entryId = "" } = useParams();
  const t = useT();
  const { data, isPending, isError, error, refetch } = useQuery({
    queryKey: qk.skillHubEntry(entryId, ""),
    queryFn: () => client.get(entryId),
    enabled: entryId !== "",
  });
  useBreadcrumbs([
    { label: t("nav.home"), to: "/" },
    { label: "Skill hub", to: "/skill-hub" },
    { label: data ? data.name : "…" },
  ]);

  if (isError) {
    // D16: "not there" says so and points back; only a failure to reach the
    // server gets 「再試一次」 (NN/g #9, GOV.UK's page-not-found pattern).
    return (
      <div className="page">
        <h1>Skill hub</h1>
        {isGone(error) ? (
          <>
            <p className="empty">{t("skillHub.notFound")}</p>
            <p>
              <Link to="/skill-hub">{t("skillHub.backToList")}</Link>
            </p>
          </>
        ) : (
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
        )}
      </div>
    );
  }
  if (isPending || !data) return <p>{t("skillHub.loading")}</p>;
  // Keyed: another skill is another page — a notice about this one's install,
  // its open tab's state, must not ride along to the next (review round 1).
  return <EntryView key={data.id} entry={data} client={client} />;
}

function EntryView({ entry, client }: { entry: SkillHubDetail; client: SkillHubApi }) {
  const t = useT();
  const [params, setParams] = useSearchParams();
  const asked = params.get("tab") as Tab | null;
  const tab: Tab = asked && TABS.includes(asked) ? asked : "readme";
  const [notice, setNotice] = useState<PageNoticeContent | null>(null);
  const tabIds = useId();
  const tabRefs = useRef<Record<Tab, HTMLButtonElement | null>>({
    readme: null,
    files: null,
    history: null,
    forks: null,
  });
  const choose = (next: Tab) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (next === "readme") out.delete("tab");
        else out.set("tab", next);
        return out;
      },
      { replace: true },
    );
  // APG tabs: ←/→ move between the tabs, and moving selects.
  const onTabKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const at = TABS.indexOf(tab);
    const next = TABS[(at + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length];
    choose(next);
    tabRefs.current[next]?.focus();
  };
  const label: Record<Tab, string> = {
    readme: t("skillHub.tab.readme"),
    files: t("skillHub.tab.files", { count: entry.files.length }),
    history: t("skillHub.tab.history"),
    forks: t("skillHub.tab.forks", { count: entry.forks.length }),
  };

  return (
    <div className="page skill-hub-entry">
      <PageNotice notice={notice} />
      <header className="skill-hub-entry-head">
        <div className="skill-hub-entry-title">
          <h1>{entry.name}</h1>
          <p className="skill-hub-entry-desc">{entry.description}</p>
          <p className="skill-hub-entry-byline">
            <UserChip userId={entry.owner} size={20} />
            {entry.forked_from ? <Lineage lineage={entry.forked_from} /> : null}
          </p>
        </div>
        {entry.is_owner ? (
          <OwnerActions entry={entry} client={client} onNotice={setNotice} />
        ) : null}
      </header>

      <div className="skill-hub-tabs" role="tablist" aria-label={entry.name} onKeyDown={onTabKey}>
        {TABS.map((id) => (
          <button
            key={id}
            ref={(el) => {
              tabRefs.current[id] = el;
            }}
            type="button"
            role="tab"
            id={`${tabIds}-${id}`}
            aria-selected={tab === id}
            aria-controls={`${tabIds}-panel`}
            tabIndex={tab === id ? 0 : -1}
            className="skill-hub-tab"
            onClick={() => choose(id)}
          >
            {label[id]}
          </button>
        ))}
      </div>

      <div className="skill-hub-entry-body">
        <div
          className="skill-hub-entry-main"
          role="tabpanel"
          id={`${tabIds}-panel`}
          aria-labelledby={`${tabIds}-${tab}`}
        >
          {tab === "readme" ? (
            <div className="skill-hub-md markdown">
              <ReactMarkdown components={UNDER_THE_TITLE}>{skillBodyUnderTitle(entry.skill_md, entry.name)}</ReactMarkdown>
            </div>
          ) : tab === "files" ? (
            <SkillHubFiles
              entryId={entry.id}
              revision={entry.revision}
              files={entry.files}
              scripts={entry.scripts}
              client={client}
            />
          ) : tab === "history" ? (
            <SkillHubHistory entry={entry} client={client} />
          ) : entry.forks.length === 0 ? (
            <p className="muted">{t("skillHub.forksOf.none")}</p>
          ) : (
            <ul className="skill-hub-list">
              {entry.forks.map((f) => (
                <SkillHubRow key={f.id} entry={f} />
              ))}
            </ul>
          )}
        </div>
        <Sidebar entry={entry} client={client} onNotice={setNotice} />
      </div>
    </div>
  );
}

/** What a person decides by, beside whatever tab they read (D5). */
function Sidebar({
  entry,
  client,
  onNotice,
}: {
  entry: SkillHubDetail;
  client: SkillHubApi;
  onNotice: (n: PageNoticeContent) => void;
}) {
  const t = useT();
  const apps = useApps();
  const appTitle = (slug: string) => apps.find((a) => a.slug === slug)?.title || slug;
  const [installing, setInstalling] = useState(false);
  const installs = useQuery({
    queryKey: qk.skillHubInstalls(entry.id),
    queryFn: () => client.installs(entry.id),
  });
  const counted = entry.installs > 0 || entry.uses > 0;
  return (
    <aside className="skill-hub-entry-side">
      <button
        type="button"
        className="btn"
        data-variant="primary"
        data-size="md"
        onClick={() => setInstalling(true)}
      >
        {t("skillHub.install")}
      </button>
      <section>
        <h2>{t("skillHub.installs.title")}</h2>
        {/* Only what is known: a failed or pending read says nothing rather
            than "you have not installed it" (an older API pod mid-rollout
            has no such route). */}
        {!installs.isSuccess ? null : installs.data.length === 0 ? (
          <p className="muted small">{t("skillHub.installs.none")}</p>
        ) : (
          <ul className="skill-hub-side-list">
            {installs.data.map((i) => (
              <li key={i.item_id}>
                <Link to={`/a/${encodeURIComponent(i.app)}/${encodeURIComponent(i.item_id)}`}>
                  {i.title || i.item_id}
                </Link>{" "}
                <span className="muted small">{appTitle(i.app)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {entry.is_owner ? (
        <section>
          <h2>{t("skillHub.side.permission")}</h2>
          <p>
            {entry.visibility === "private"
              ? t("skillHub.visibility.private")
              : entry.visibility === "restricted"
                ? t("skillHub.visibility.restricted")
                : t("skillHub.visibility.public")}
          </p>
        </section>
      ) : null}
      {entry.updated_at ? (
        <section>
          <h2>{t("skillHub.side.updated")}</h2>
          <p>{ymd(entry.updated_at)}</p>
        </section>
      ) : null}
      {counted ? (
        <section>
          <h2>{t("skillHub.side.usage")}</h2>
          <p>{countsText(t, entry.installs, entry.uses)}</p>
          {entry.counted_since ? (
            <p className="muted small">
              {t("skillHub.countedSince", { day: entry.counted_since })}
            </p>
          ) : null}
        </section>
      ) : null}
      <section>
        <h2>{t("skillHub.side.source")}</h2>
        <p>{appTitle(entry.source_app)}</p>
      </section>
      <section id="tools">
        <h2>{t("skillHub.tools")}</h2>
        {entry.referenced_tools.length === 0 ? (
          <p className="muted small">{t("skillHub.tools.none")}</p>
        ) : (
          <ul className="skill-hub-side-list">
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
          <p className="muted small">{t("skillHub.review.ok")}</p>
        ) : (
          <ul className="skill-hub-notes">
            {entry.review.notes.map((note) => (
              <li key={note} className="markdown">
                <ReactMarkdown>{note}</ReactMarkdown>
              </li>
            ))}
          </ul>
        )}
        {entry.review.model ? (
          <p className="muted small">{t("skillHub.review.by", { model: entry.review.model })}</p>
        ) : null}
      </section>
      {installing ? (
        <InstallDialog
          entry={entry}
          client={client}
          onClose={() => setInstalling(false)}
          onDone={(slug, itemId, title) => {
            setInstalling(false);
            onNotice({
              kind: "success",
              text: t("skillHub.install.done", { title }),
              link: {
                to: `/a/${encodeURIComponent(slug)}/${encodeURIComponent(itemId)}`,
                label: t("skillHub.install.open"),
              },
            });
          }}
        />
      ) : null}
    </aside>
  );
}

/** 「安裝到 workspace…」 (D6): an App, then one of its workspaces the viewer
 * may edit, each saying beforehand what installing would do there — the
 * same refusal the install route gives, asked first. A pick is cheap to redo,
 * so the close is not guarded (#779); it is a form, so no backdrop close. */
function InstallDialog({
  entry,
  client,
  onClose,
  onDone,
}: {
  entry: SkillHubDetail;
  client: SkillHubApi;
  onClose: () => void;
  onDone: (slug: string, itemId: string, title: string) => void;
}) {
  const t = useT();
  const titleId = useId();
  const apps = useApps();
  const users = useUsers();
  const nameOf = (id: string) => users.find((u) => u.id === id)?.name ?? id;
  const qc = useQueryClient();
  const [slug, setSlug] = useState(entry.source_app);
  const [picked, setPicked] = useState<string | null>(null);
  const targets = useQuery({
    queryKey: qk.skillHubTargets(entry.id, slug),
    queryFn: () => client.targets(entry.id, slug),
  });
  const items = targets.data?.items ?? [];
  const install = useMutation({
    mutationFn: (itemId: string) => client.install(slug, itemId, entry.id),
    onSuccess: (_res, itemId) => {
      invalidateHubInstalls(qc);
      void qc.invalidateQueries({ queryKey: qk.itemSkills(slug, itemId) });
      onDone(slug, itemId, items.find((i) => i.item_id === itemId)?.title ?? itemId);
    },
    meta: { silentError: true },
  });
  const missing = targets.data?.missing_tools ?? [];
  return (
    <ModalShell onClose={onClose} labelledBy={titleId} width={520} data-testid="skill-hub-install">
      <h2 id={titleId} className="modal-title">
        {t("skillHub.install.title", { name: entry.name })}
      </h2>
      <label className="skill-hub-fork-app">
        <span>{t("skillHub.install.app")}</span>
        <select
          className="input"
          value={slug}
          onChange={(e) => {
            setSlug(e.target.value);
            setPicked(null);
          }}
        >
          {(apps.some((a) => a.slug === slug) ? apps : [{ slug, title: slug }, ...apps]).map((a) => (
            <option key={a.slug} value={a.slug}>
              {a.title || a.slug}
            </option>
          ))}
        </select>
      </label>
      {missing.length > 0 ? (
        <p className="hint">{t("skillHub.install.missing", { tools: missing.join(", ") })}</p>
      ) : null}
      <fieldset className="skill-hub-fork-items">
        <legend>{t("skillHub.install.items")}</legend>
        {targets.isPending ? (
          <p className="muted">{t("skillHub.loading")}</p>
        ) : targets.isError ? (
          <p className="error" role="alert">
            {t("skillHub.failed", { reason: describeRefusal(targets.error, t) })}
          </p>
        ) : items.length === 0 ? (
          <p className="muted">{t("skillHub.install.none")}</p>
        ) : (
          items.map((it) => (
            <label key={it.item_id} className="skill-hub-fork-item">
              <input
                type="radio"
                name={`${titleId}-item`}
                value={it.item_id}
                checked={picked === it.item_id}
                disabled={it.state !== "ok"}
                onChange={() => setPicked(it.item_id)}
              />
              <span>{it.title || it.item_id}</span>
              {it.state === "installed" ? (
                <span className="muted small">{t("skillHub.install.state.installed")}</span>
              ) : it.state === "unavailable" ? (
                <span className="muted small">{t("skillHub.install.state.unavailable")}</span>
              ) : it.state === "name_taken" ? (
                <span className="muted small">
                  {it.owner
                    ? t("skillHub.install.state.taken", { owner: nameOf(it.owner) })
                    : t("skillHub.install.state.takenHand")}
                </span>
              ) : null}
            </label>
          ))
        )}
      </fieldset>
      {install.isError ? (
        <p className="error" role="alert">
          {t("skillHub.failed", { reason: describeRefusal(install.error, t) })}
        </p>
      ) : null}
      <ModalActions>
        <button type="button" className="btn" data-variant="secondary" onClick={onClose}>
          {t("skillHub.cancel")}
        </button>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          disabled={!picked || install.isPending}
          onClick={() => picked && install.mutate(picked)}
        >
          {t("skillHub.install.confirm")}
        </button>
      </ModalActions>
    </ModalShell>
  );
}

/** What this entry was forked from, as the viewer may know it. A root that
 * went private or was deleted is SAID — the plan's 「原作已下架 / 已刪除」 —
 * never a broken link. */
function Lineage({ lineage }: { lineage: NonNullable<SkillHubDetail["forked_from"]> }) {
  const t = useT();
  if (lineage.state === "live") {
    return (
      <span className="skill-hub-lineage">
        <Link to={`/skill-hub/${encodeURIComponent(lineage.entry)}`}>
          <ForkOf owner={lineage.owner} name={lineage.name} />
        </Link>
      </span>
    );
  }
  return (
    <span className="skill-hub-lineage muted">
      {lineage.state === "unpublished"
        ? t("skillHub.origin.unpublished")
        : t("skillHub.origin.deleted")}
    </span>
  );
}

/** The owner's five actions. Rendered for the owner ONLY — the caller gates
 * on `entry.is_owner`, the server's answer, and every one of these routes
 * refuses a non-owner anyway. */
function OwnerActions({
  entry,
  client,
  onNotice,
}: {
  entry: SkillHubDetail;
  client: SkillHubApi;
  onNotice: (n: PageNoticeContent) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { confirm } = useDialog();
  const pickableGroups = usePickableGroups();
  const [sharing, setSharing] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [newItem, setNewItem] = useState<SkillEditTarget | null>(null);
  // The last failure and WHICH action it came from: it clears when any
  // action starts (`onMutate`), and each dialog draws only a failure of its
  // own — round 3 of #826: clearing on dialog OPEN threw a page-level failure
  // away, and a share failure outlived the retry that succeeded.
  const [failure, setFailure] = useState<{ action: string; text: string } | null>(null);
  const users = useUsers();
  // The notice names the new owner the way the picker did (display name),
  // falling back to the id for someone the directory does not list.
  const personName = (id: string) => users.find((u) => u.id === id)?.name ?? id;
  const refresh = () => qc.invalidateQueries({ queryKey: ["skillHub"] });
  const failed = (action: string) => (e: unknown) =>
    setFailure({ action, text: describeRefusal(e, t) });
  const starting = { onMutate: () => setFailure(null) };
  // Every action here shows its own failure line (`failed`), so the query
  // client's global write-failure toast must not fire for it too — the demo
  // showed both at once, the toast under a title that was not even true
  // (plan-skill-hub-ui-polish D3). `silentError` is that opt-out.
  const own = { meta: { silentError: true } } as const;
  // The list is where a transfer or a delete leaves the person; it says what
  // just happened (D10) — a success is a notice there, not a silent bounce.
  const leaveWith = (text: string) =>
    navigate("/skill-hub", {
      replace: true,
      state: { notice: { kind: "success", text } },
    });

  const wasPrivate = entry.visibility === "private";
  const unpublish = useMutation({
    mutationFn: () => (wasPrivate ? client.republish(entry.id) : client.unpublish(entry.id)),
    // D10 (NN/g #1): say it happened — the only other sign was a word in the sidebar.
    onSuccess: () => {
      void refresh();
      onNotice({
        kind: "success",
        text: t(wasPrivate ? "skillHub.republished" : "skillHub.unpublished", { name: entry.name }),
      });
    },
    onError: failed("unpublish"),
    ...starting,
    ...own,
  });
  const permission = useMutation({
    mutationFn: (perm: Parameters<SkillHubApi["setPermission"]>[1]) =>
      client.setPermission(entry.id, perm),
    onSuccess: () => {
      setSharing(false);
      void refresh();
    },
    onError: failed("permission"),
    ...starting,
    ...own,
  });
  const transfer = useMutation({
    mutationFn: (owner: string) => client.transfer(entry.id, owner),
    onSuccess: (_res, owner) => {
      setTransferring(false);
      void refresh();
      // Given away: the page would refetch an entry its old owner may no
      // longer read (a private one) and land on the error line with a Retry
      // that cannot succeed. The list is where they are now — told who has
      // it, and that a private one is out of their sight from here on.
      leaveWith(
        t(
          entry.visibility === "private"
            ? "skillHub.transferred.private"
            : "skillHub.transferred",
          {
            name: entry.name,
            owner: personName(owner),
          },
        ),
      );
    },
    onError: failed("transfer"),
    ...starting,
    ...own,
  });
  const remove = useMutation({
    mutationFn: () => client.remove(entry.id),
    onSuccess: () => {
      void refresh();
      leaveWith(t("skillHub.deleted", { name: entry.name }));
    },
    onError: failed("remove"),
    ...starting,
    ...own,
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
    onError: failed("edit"),
    ...starting,
    ...own,
  });

  // D10 (NN/g #5): unpublishing takes the skill away from everyone else, so
  // it asks first and says what happens to the copies already installed.
  // Republishing gives nothing away and does not ask.
  const askUnpublish = async () => {
    if (wasPrivate) {
      unpublish.mutate();
      return;
    }
    const choice = await confirm({
      title: t("skillHub.unpublish.title", { name: entry.name }),
      body: (
        <>
          <p>{t("skillHub.unpublish.body")}</p>
          <p>{t("skillHub.impact.installed")}</p>
        </>
      ),
      actions: [
        { id: "cancel", label: t("skillHub.cancel") },
        { id: "unpublish", label: t("skillHub.unpublish.confirm"), variant: "danger" },
      ],
    });
    if (choice === "unpublish") unpublish.mutate();
  };
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
    <div className="skill-hub-actions">
      <ActionMenu
        label={t("skillHub.manage")}
        disabled={busy}
        items={[
          { id: "edit", label: t("skillHub.edit"), onSelect: () => edit.mutate() },
          {
            id: "unpublish",
            label: wasPrivate ? t("skillHub.republish") : t("skillHub.unpublish"),
            onSelect: () => void askUnpublish(),
          },
          { id: "share", label: t("skillHub.share"), onSelect: () => setSharing(true) },
          { id: "transfer", label: t("skillHub.transfer"), onSelect: () => setTransferring(true) },
          { id: "delete", label: t("skillHub.delete"), onSelect: () => void askDelete(), danger: true },
        ]}
      />
      {/* A dialog draws its own failure (round 2 of #826: a line behind the
          backdrop was invisible); the page draws the rest, and only while no
          dialog covers it. */}
      {failure && !sharing && !transferring ? (
        <p className="error" role="alert">
          {t("skillHub.failed", { reason: failure.text })}
        </p>
      ) : null}

      {sharing && entry.permission ? (
        <PermissionDialog
          // The skill's name, as the page shows it — not `owner/name` (audit #30).
          resourceName={entry.name}
          owner={entry.owner}
          value={entry.permission}
          roles={DOC_ROLES}
          // The one sentence on installed copies, the 下架 confirm's own (D10).
          caption={`${t("skillHub.share.caption")}${t("skillHub.impact.installed")}`}
          // A hub entry is public to everyone on the platform, not to "this
          // workspace" (D12).
          audience="platform"
          pickableGroups={pickableGroups}
          busy={permission.isPending}
          error={failure?.action === "permission" ? t("skillHub.failed", { reason: failure.text }) : null}
          onSubmit={(perm) => permission.mutate(perm)}
          onClose={() => setSharing(false)}
        />
      ) : null}

      {transferring ? (
        <TransferDialog
          name={entry.name}
          owner={entry.owner}
          busy={transfer.isPending}
          error={failure?.action === "transfer" ? t("skillHub.failed", { reason: failure.text }) : null}
          onSubmit={(owner) => transfer.mutate(owner)}
          onClose={() => setTransferring(false)}
        />
      ) : null}

      {newItem ? (
        <NewItemDialog
          target={newItem}
          entryId={entry.id}
          onClose={() => setNewItem(null)}
        />
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
  error,
  onSubmit,
  onClose,
}: {
  name: string;
  owner: string;
  busy: boolean;
  /** The last attempt's refusal, worded — shown here, where the person is. */
  error: string | null;
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
      <p className="hint skill-hub-dialog-hint">{t("skillHub.transfer.body")}</p>
      <UserPicker
        selected={picked ? [picked] : []}
        onToggle={(id) => setPicked((cur) => (cur === id ? null : id))}
        exclude={[owner]}
      />
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
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
function NewItemDialog({
  target,
  entryId,
  onClose,
}: {
  target: SkillEditTarget;
  entryId: string;
  onClose: () => void;
}) {
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
        {/* The profile the skill was written for rides along (`?profile=`):
            the new item opens on it rather than the App's default. So does
            the entry (`?skill=`), for the form to say what comes next
            (plan-skill-hub-ui-polish D11). */}
        {/* Sized like the buttons beside it: `.btn` alone has no padding, and
            a link does not get a button's (audit #29). */}
        <Link
          className="btn"
          data-variant="primary"
          data-size="md"
          to={`/a/${encodeURIComponent(target.app)}/new?profile=${encodeURIComponent(target.profile)}&skill=${encodeURIComponent(entryId)}`}
        >
          {t("skillHub.edit.newItem.go")}
        </Link>
      </ModalActions>
    </ModalShell>
  );
}

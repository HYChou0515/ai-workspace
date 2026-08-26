/**
 * #608 — the Groups management page. A superuser creates groups and designates an
 * owner; the owner manages members + maintainers, can transfer ownership and
 * delete; a maintainer manages MEMBERS only. Capabilities per group come from the
 * pure `groupCapabilities` (mirrors the backend gates); the server re-checks.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { type Group, type GroupsApi, groupsApi } from "../api/groups";
import { exactTime, relativeTime } from "../api/types";
import { qk } from "../api/queryKeys";
import { Icon } from "../components/Icon";
import { UserChip } from "../components/UserChip";
import { UserPicker } from "../components/UserPicker";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { useIsSuperuser } from "../hooks/useIsSuperuser";
import { groupCapabilities, groupRoleLabel } from "../lib/groupRole";
import { pxToRem } from "../lib/pxToRem";

type SortKey = "name" | "owner" | "members" | "updated";
type SortState = { by: SortKey; dir: "asc" | "desc" };

/** Does this group answer what was typed? Name, description and owner — the
 * three things a person remembers a group by. */
function matchesGroup(g: Group, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (needle === "") return true;
  return (
    g.name.toLowerCase().includes(needle) ||
    g.description.toLowerCase().includes(needle) ||
    (g.owner ?? "").toLowerCase().includes(needle)
  );
}

/** Sort a copy — the query cache's array is shared, and sorting it in place
 * mutates what every other consumer of `qk.groups` sees. Text compares with
 * `localeCompare` (a code-point `<` puts every lowercase name last); the member
 * count compares as a NUMBER, or "10" would sort before "2". */
function sortGroups(groups: Group[], { by, dir }: SortState): Group[] {
  const sign = dir === "asc" ? 1 : -1;
  const text = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });
  return groups.slice().sort((a, b) => {
    const cmp =
      by === "members"
        ? a.members.length - b.members.length
        : by === "updated"
          ? // Epoch ms, so numeric — as text it would order by first digit.
            // Negated because "asc" on a time column means MOST RECENT first:
            // recency is the question being asked, and leading with the stalest
            // row makes everyone click twice.
            b.updated_at - a.updated_at
          : by === "owner"
            ? text(a.owner ?? "", b.owner ?? "")
            : text(a.name, b.name);
    // Ties fall back to name, so the order is stable and reproducible rather
    // than dependent on the input order.
    return sign * (cmp || text(a.name, b.name));
  });
}

function SortHeader({
  id,
  label,
  sort,
  onSort,
  style,
}: {
  id: SortKey;
  label: string;
  sort: SortState;
  onSort: (by: SortKey) => void;
  style?: React.CSSProperties;
}) {
  const active = sort.by === id;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      style={{ ...th, ...style }}
    >
      <button
        type="button"
        data-testid={`groups-sort-${id}`}
        onClick={() => onSort(id)}
        style={{ font: "inherit", color: "inherit", display: "inline-flex", gap: 4, alignItems: "center" }}
      >
        {label}
        <span aria-hidden style={{ opacity: active ? 1 : 0.25 }}>
          {active && sort.dir === "desc" ? "↓" : "↑"}
        </span>
      </button>
    </th>
  );
}

export function GroupsPage({ client = groupsApi }: { client?: GroupsApi }) {
  const me = useCurrentUser();
  const isSuperuser = useIsSuperuser();
  const qc = useQueryClient();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortState>({ by: "name", dir: "asc" });

  const { data: groups = [], isPending } = useQuery({
    queryKey: qk.groups,
    queryFn: () => client.listGroups(),
  });
  const invalidate = () => void qc.invalidateQueries({ queryKey: qk.groups });

  const toggleSort = (by: SortKey) =>
    setSort((s) => ({ by, dir: s.by === by && s.dir === "asc" ? "desc" : "asc" }));
  const shown = sortGroups(
    groups.filter((g) => matchesGroup(g, q)),
    sort,
  );

  return (
    <div style={page}>
      <header style={headRow}>
        <div>
          <h1 style={{ margin: 0, fontSize: pxToRem(20) }}>Groups</h1>
          <p style={{ color: "var(--text-paper-d)", fontSize: pxToRem(13), margin: "4px 0 0" }}>
            Share with a whole team at once — grant a group and every member gets access.
          </p>
        </div>
        {isSuperuser && (
          <button
            type="button"
            data-testid="groups-new"
            className="btn"
            data-variant="primary"
            onClick={() => setCreating(true)}
          >
            <Icon name="users" size={14} /> New group
          </button>
        )}
      </header>

      {isPending ? (
        <div style={{ color: "var(--text-paper-d)" }}>Loading…</div>
      ) : groups.length === 0 ? (
        <div style={{ color: "var(--text-paper-d)" }}>
          {isSuperuser ? "No groups yet — create one." : "You don't belong to any groups yet."}
        </div>
      ) : (
        <>
          <input
            type="search"
            data-testid="groups-search"
            className="kb-input"
            aria-label="Search groups"
            placeholder="Search groups…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {shown.length === 0 ? (
            <div style={{ color: "var(--text-paper-d)" }}>No groups match “{q}”.</div>
          ) : (
            <table style={table}>
              <thead>
                <tr>
                  <SortHeader
                    id="name"
                    label="Name"
                    sort={sort}
                    onSort={toggleSort}
                    style={{ textAlign: "left" }}
                  />
                  <SortHeader id="owner" label="Owner" sort={sort} onSort={toggleSort} />
                  <SortHeader
                    id="members"
                    label="Members"
                    sort={sort}
                    onSort={toggleSort}
                    style={{ textAlign: "right" }}
                  />
                  <SortHeader
                    id="updated"
                    label="Last updated"
                    sort={sort}
                    onSort={toggleSort}
                  />
                  <th style={{ ...th, width: 1 }} />
                </tr>
              </thead>
              <tbody>
                {shown.map((g) => (
                  <GroupRow
                    key={g.resource_id}
                    group={g}
                    me={me}
                    isSuperuser={isSuperuser}
                    editing={editingId === g.resource_id}
                    onEdit={() => setEditingId(editingId === g.resource_id ? null : g.resource_id)}
                    client={client}
                    onChanged={invalidate}
                  />
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {creating && (
        <CreateGroupModal
          client={client}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            invalidate();
          }}
        />
      )}
    </div>
  );
}

function GroupRow({
  group,
  me,
  isSuperuser,
  editing,
  onEdit,
  client,
  onChanged,
}: {
  group: Group;
  me: string;
  isSuperuser: boolean;
  editing: boolean;
  onEdit: () => void;
  client: GroupsApi;
  onChanged: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const caps = groupCapabilities(group, me, isSuperuser);
  const role = groupRoleLabel(group, me, isSuperuser);
  const n = group.members.length;
  return (
    <>
      <tr data-testid={`group-row-${group.resource_id}`} data-group-name={group.name}>
        <td style={{ ...td, minWidth: 0 }}>
          {renaming ? (
            <GroupNameEditor
              group={group}
              client={client}
              onDone={() => setRenaming(false)}
              onChanged={onChanged}
            />
          ) : (
            <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
              <span style={{ fontWeight: 600 }}>{group.name}</span>
              {caps.canManageGroup && (
                <button
                  type="button"
                  className="btn"
                  data-variant="ghost"
                  data-size="sm"
                  data-testid={`group-rename-${group.resource_id}`}
                  aria-label={`Rename ${group.name}`}
                  onClick={() => setRenaming(true)}
                >
                  <Icon name="pencil" size={12} />
                </button>
              )}
            </div>
          )}
          {group.description && (
            <div style={{ color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
              {group.description}
            </div>
          )}
          {role && (
            <span style={{ ...chip, display: "inline-block", marginTop: 4 }}>{role}</span>
          )}
        </td>
        <td style={{ ...td, whiteSpace: "nowrap" }}>
          {group.owner && <UserChip userId={group.owner} size={16} />}
        </td>
        <td style={{ ...td, textAlign: "right", whiteSpace: "nowrap" }}>
          <span style={{ color: "var(--text-paper-d)", fontSize: pxToRem(12) }}>
            {n} member{n === 1 ? "" : "s"}
          </span>
        </td>
        <td
          style={{ ...td, whiteSpace: "nowrap", color: "var(--text-paper-d)", fontSize: pxToRem(12) }}
          data-testid={`group-updated-${group.resource_id}`}
          // The exact stamp on hover. The cell drops precision on purpose —
          // "3 d ago" answers "is this current?" better than a datetime does —
          // so the precision it drops has to stay one hover away.
          title={group.updated_at ? exactTime(new Date(group.updated_at).toISOString()) : ""}
        >
          {group.updated_at ? relativeTime(new Date(group.updated_at).toISOString()) : "—"}
        </td>
        <td style={{ ...td, textAlign: "right" }}>
          {caps.canManageMembers && (
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              aria-label={`Edit ${group.name}`}
              onClick={onEdit}
            >
              Edit
            </button>
          )}
        </td>
      </tr>
      {editing && (
        <tr>
          {/* The editor is wide — it spans the row rather than being crushed
              into the name column. */}
          <td colSpan={5} style={{ ...td, paddingTop: 0 }}>
            <GroupEditor group={group} caps={caps} client={client} onChanged={onChanged} />
          </td>
        </tr>
      )}
    </>
  );
}

/** Inline rename. Enter commits, Escape / blur abandons — the same shape the chat
 * rail's rename uses, so the gesture is the one people already know here. */
function GroupNameEditor({
  group,
  client,
  onDone,
  onChanged,
}: {
  group: Group;
  client: GroupsApi;
  onDone: () => void;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState(group.name);
  const rename = useMutation({
    mutationFn: (name: string) => client.renameGroup(group.resource_id, name),
    onSuccess: () => {
      onChanged();
      onDone();
    },
  });

  const commit = () => {
    const next = draft.trim();
    // Blank is a 422 server-side and an unchanged name is a no-op write — neither
    // is worth a round trip, and both would flash an error for nothing.
    if (!next || next === group.name) {
      onDone();
      return;
    }
    rename.mutate(next);
  };

  return (
    <input
      // eslint-disable-next-line jsx-a11y/no-autofocus
      autoFocus
      className="inline-edit"
      aria-label="Group name"
      data-testid={`group-name-input-${group.resource_id}`}
      value={draft}
      disabled={rename.isPending}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") onDone();
      }}
      onBlur={onDone}
      style={{ fontWeight: 600, width: "100%" }}
    />
  );
}

function GroupEditor({
  group,
  caps,
  client,
  onChanged,
}: {
  group: Group;
  caps: { canManageMembers: boolean; canManageGroup: boolean };
  client: GroupsApi;
  onChanged: () => void;
}) {
  const addMembers = useMutation({
    mutationFn: (id: string) => client.addMembers(group.resource_id, [id]),
    onSuccess: onChanged,
  });
  const removeMember = useMutation({
    mutationFn: (id: string) => client.removeMember(group.resource_id, id),
    onSuccess: onChanged,
  });
  const addMaintainers = useMutation({
    mutationFn: (id: string) => client.addMaintainers(group.resource_id, [id]),
    onSuccess: onChanged,
  });
  const removeMaintainer = useMutation({
    mutationFn: (id: string) => client.removeMaintainer(group.resource_id, id),
    onSuccess: onChanged,
  });
  const transfer = useMutation({
    mutationFn: (id: string) => client.transferOwner(group.resource_id, id),
    onSuccess: onChanged,
  });
  const del = useMutation({
    mutationFn: () => client.deleteGroup(group.resource_id),
    onSuccess: onChanged,
  });

  return (
    <div style={editor}>
      <Roster
        title="Members"
        ids={group.members}
        addTestId="group-members-add"
        pickerTestId="group-members-picker"
        exclude={[...group.members, group.owner ?? "", ...group.maintainers]}
        canManage={caps.canManageMembers}
        onAdd={(id) => addMembers.mutate(id)}
        onRemove={(id) => removeMember.mutate(id)}
        groupName={group.name}
      />
      {caps.canManageGroup && (
        <>
          <Roster
            title="Maintainers"
            hint="Can manage members on your behalf"
            ids={group.maintainers}
            addTestId="group-maintainers-add"
            pickerTestId="group-maintainers-picker"
            exclude={[...group.maintainers, group.owner ?? "", ...group.members]}
            canManage
            onAdd={(id) => addMaintainers.mutate(id)}
            onRemove={(id) => removeMaintainer.mutate(id)}
            groupName={group.name}
          />
          <TransferControl
            currentOwner={group.owner ?? ""}
            exclude={[group.owner ?? ""]}
            onTransfer={(id) => transfer.mutate(id)}
          />
          <button
            type="button"
            data-testid="group-delete"
            className="btn"
            data-variant="danger"
            data-size="sm"
            onClick={() => del.mutate()}
          >
            Delete group
          </button>
        </>
      )}
    </div>
  );
}

function Roster({
  title,
  hint,
  ids,
  addTestId,
  pickerTestId,
  exclude,
  canManage,
  onAdd,
  onRemove,
  groupName,
}: {
  title: string;
  hint?: string;
  ids: string[];
  addTestId: string;
  pickerTestId: string;
  exclude: string[];
  canManage: boolean;
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
  groupName: string;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <section style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="caps">{title}</span>
        {hint && (
          <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>{hint}</span>
        )}
        {canManage && (
          <button
            type="button"
            data-testid={addTestId}
            className="btn"
            data-variant="ghost"
            data-size="sm"
            onClick={() => setPicking((p) => !p)}
          >
            + Add
          </button>
        )}
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexWrap: "wrap", gap: 6 }}>
        {ids.length === 0 && (
          <li style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(12) }}>None yet</li>
        )}
        {ids.map((id) => (
          <li key={id} style={memberPill}>
            <UserChip userId={id} size={18} />
            {canManage && (
              <button
                type="button"
                className="tag-remove"
                aria-label={`Remove ${id} from ${groupName}`}
                onClick={() => onRemove(id)}
              >
                <Icon name="x" size={12} />
              </button>
            )}
          </li>
        ))}
      </ul>
      {picking && (
        <div data-testid={pickerTestId} style={pickerBox}>
          <UserPicker
            selected={[]}
            exclude={exclude}
            onToggle={(id) => {
              onAdd(id);
              setPicking(false);
            }}
          />
        </div>
      )}
    </section>
  );
}

function TransferControl({
  currentOwner,
  exclude,
  onTransfer,
}: {
  currentOwner: string;
  exclude: string[];
  onTransfer: (id: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <section style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className="caps">Ownership</span>
        <span style={{ color: "var(--text-paper-d2)", fontSize: pxToRem(11) }}>
          owner {currentOwner && <UserChip userId={currentOwner} size={16} />}
        </span>
        <button
          type="button"
          data-testid="group-transfer"
          className="btn"
          data-variant="ghost"
          data-size="sm"
          onClick={() => setPicking((p) => !p)}
        >
          Transfer ownership
        </button>
      </div>
      {picking && (
        <div data-testid="group-transfer-picker" style={pickerBox}>
          <UserPicker
            selected={[]}
            exclude={exclude}
            onToggle={(id) => {
              onTransfer(id);
              setPicking(false);
            }}
          />
        </div>
      )}
    </section>
  );
}

function CreateGroupModal({
  client,
  onClose,
  onCreated,
}: {
  client: GroupsApi;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [picking, setPicking] = useState(false);
  const create = useMutation({
    mutationFn: () => client.createGroup({ name, description, owner }),
    onSuccess: onCreated,
  });
  const canCreate = name.trim().length > 0 && owner.length > 0;

  return (
    <div role="dialog" aria-label="New group" style={modalOverlay} onClick={onClose}>
      <div style={modalCard} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ margin: "0 0 12px", fontSize: pxToRem(16) }}>New group</h2>
        <label style={field}>
          <span className="caps">Name</span>
          <input
            aria-label="Group name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={input}
          />
        </label>
        <label style={field}>
          <span className="caps">Description</span>
          <input
            aria-label="Group description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            style={input}
          />
        </label>
        <div style={field}>
          <span className="caps">Owner</span>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {owner ? <UserChip userId={owner} size={20} /> : <span style={{ color: "var(--text-paper-d2)" }}>Pick an owner</span>}
            <button
              type="button"
              data-testid="group-owner-pick"
              className="btn"
              data-variant="ghost"
              data-size="sm"
              onClick={() => setPicking((p) => !p)}
            >
              {owner ? "Change" : "Pick owner"}
            </button>
          </div>
          {picking && (
            <div data-testid="group-owner-picker" style={pickerBox}>
              <UserPicker
                selected={owner ? [owner] : []}
                onToggle={(id) => {
                  setOwner(id);
                  setPicking(false);
                }}
              />
            </div>
          )}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button type="button" className="btn" data-variant="ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={!canCreate || create.isPending}
            onClick={() => create.mutate()}
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

const page: React.CSSProperties = { maxWidth: 720, margin: "0 auto", padding: 24, display: "grid", gap: 16 };
const headRow: React.CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 };
const table: React.CSSProperties = { width: "100%", borderCollapse: "collapse" };
const th: React.CSSProperties = {
  textAlign: "right",
  padding: "6px 8px",
  borderBottom: "1px solid var(--paper-3)",
  color: "var(--text-paper-d)",
  fontSize: pxToRem(12),
  fontWeight: 600,
  whiteSpace: "nowrap",
};
const td: React.CSSProperties = {
  padding: "8px",
  borderBottom: "1px solid var(--paper-3)",
  verticalAlign: "top",
};
const chip: React.CSSProperties = { fontSize: pxToRem(11), padding: "1px 8px", borderRadius: 999, background: "var(--paper-2)", color: "var(--text-paper-d)" };
const editor: React.CSSProperties = { marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--paper-3)", display: "grid", gap: 14 };
const pickerBox: React.CSSProperties = { border: "1px solid var(--paper-3)", borderRadius: 6, padding: 6 };
const memberPill: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  padding: "3px 6px 3px 8px",
  borderRadius: 999,
  background: "var(--paper-2)",
  fontSize: pxToRem(12),
};
const modalOverlay: React.CSSProperties = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 };
const modalCard: React.CSSProperties = { background: "var(--paper)", borderRadius: 10, padding: 20, width: "min(440px, 92vw)", display: "grid" };
const field: React.CSSProperties = { display: "grid", gap: 4, marginBottom: 10 };
const input: React.CSSProperties = { padding: "8px 10px", borderRadius: 6, border: "1px solid var(--paper-3)", background: "var(--paper)", color: "var(--text-paper)" };

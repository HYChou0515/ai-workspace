/**
 * #608 — logical Group management client. A cohesive sub-domain (like `kbApi`),
 * injected into the /groups page + the share dialogs so those are unit-testable
 * with a stub. The backend governance model (superuser creates + designates an
 * owner; owner manages members/maintainers, transfers, deletes; maintainers
 * manage members only) is enforced server-side — this is just the wire.
 */

import { API_PREFIX, apiFetch } from "./http";

/** A group as the management page sees it. `owner` is the effective owner. */
export type Group = {
  resource_id: string;
  name: string;
  description: string;
  members: string[];
  owner: string | null;
  maintainers: string[];
};

/** A group as the share-dialog picker sees it — enough to grant to, never the
 * member ids (the endpoint is world-readable). */
export type PickableGroup = {
  resource_id: string;
  name: string;
  description: string;
  member_count: number;
};

export type GroupsApi = {
  /** Groups the caller owns / maintains / belongs to (the management list). */
  listGroups(): Promise<Group[]>;
  /** Every group, name + count, for the share picker (world-readable). */
  listPickableGroups(): Promise<PickableGroup[]>;
  /** superuser-only; designates `owner`. */
  createGroup(body: { name: string; description?: string; owner: string }): Promise<Group>;
  addMembers(groupId: string, userIds: string[]): Promise<void>;
  removeMember(groupId: string, userId: string): Promise<void>;
  addMaintainers(groupId: string, userIds: string[]): Promise<void>;
  removeMaintainer(groupId: string, userId: string): Promise<void>;
  transferOwner(groupId: string, owner: string): Promise<Group>;
  deleteGroup(groupId: string): Promise<void>;
};

async function ok(resp: Response, what: string): Promise<Response> {
  if (!resp.ok) throw new Error(`${what} failed (${resp.status})`);
  return resp;
}

const gid = (id: string) => encodeURIComponent(id);
const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const groupsApi: GroupsApi = {
  async listGroups() {
    return (await ok(await apiFetch("/groups"), "list groups")).json();
  },
  async listPickableGroups() {
    return (await ok(await apiFetch("/groups/pickable"), "list pickable groups")).json();
  },
  async createGroup(body) {
    return (await ok(await apiFetch("/groups", jsonInit("POST", body)), "create group")).json();
  },
  async addMembers(groupId, userIds) {
    await ok(
      await apiFetch(`/groups/${gid(groupId)}/members`, jsonInit("POST", { user_ids: userIds })),
      "add members",
    );
  },
  async removeMember(groupId, userId) {
    await ok(
      await apiFetch(`/groups/${gid(groupId)}/members/${gid(userId)}`, { method: "DELETE" }),
      "remove member",
    );
  },
  async addMaintainers(groupId, userIds) {
    await ok(
      await apiFetch(
        `/groups/${gid(groupId)}/maintainers`,
        jsonInit("POST", { user_ids: userIds }),
      ),
      "add maintainers",
    );
  },
  async removeMaintainer(groupId, userId) {
    await ok(
      await apiFetch(`/groups/${gid(groupId)}/maintainers/${gid(userId)}`, { method: "DELETE" }),
      "remove maintainer",
    );
  },
  async transferOwner(groupId, owner) {
    return (
      await ok(
        await apiFetch(`/groups/${gid(groupId)}/owner`, jsonInit("PUT", { owner })),
        "transfer owner",
      )
    ).json();
  },
  async deleteGroup(groupId) {
    await ok(await apiFetch(`/groups/${gid(groupId)}`, { method: "DELETE" }), "delete group");
  },
};

// Referenced so the API_PREFIX import is retained if the file is tree-shaken in
// isolation (apiFetch already prefixes; kept for parity with the other clients).
void API_PREFIX;

/** In-memory mock for tests / mock mode — a stub each test overrides per case. */
export const mockGroupsApi: GroupsApi = {
  async listGroups() {
    return [];
  },
  async listPickableGroups() {
    return [];
  },
  async createGroup(body) {
    return {
      resource_id: `group:${body.name}`,
      name: body.name,
      description: body.description ?? "",
      members: [],
      owner: body.owner,
      maintainers: [],
    };
  },
  async addMembers() {},
  async removeMember() {},
  async addMaintainers() {},
  async removeMaintainer() {},
  async transferOwner(groupId, owner) {
    return { resource_id: groupId, name: "", description: "", members: [], owner, maintainers: [] };
  },
  async deleteGroup() {},
};

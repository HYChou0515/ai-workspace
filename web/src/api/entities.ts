/**
 * Entity framework API (#419) — the file-first structured records inside an
 * item's workspace (`issues/N.md`, `milestones/N.md`, …). A record is an
 * ordinary Markdown file with typed frontmatter; the backend scans + projects
 * them (computing backref / rollup on read), so there is no derived index.
 *
 * Every read/write rides the per-item entity routes; the write path (create /
 * update) is shared by the UI, the agent, and workflows — the FE never edits a
 * record's frontmatter by hand, it POSTs args / PUTs a patch.
 */

import { apiFetch } from "./http";

const enc = encodeURIComponent;

/** One parse/lint finding (§E). `error` drops the record from the projection;
 * `warning` still projects. */
export type EntityDiagnostic = {
  level: "error" | "warning";
  message: string;
  field?: string | null;
};

/** A schema field's semantic role — drives the widget + how the view binds it. */
export type EntityRole =
  | "text"
  | "status"
  | "actor"
  | "date"
  | "daterange"
  | "progress"
  | "rank"
  | "ref"
  | "backref"
  | "rollup";

/** A schema field's role + relational wiring (for the view renderer). */
export type EntityFieldSpec = {
  name: string;
  role: EntityRole;
  required?: boolean;
  values?: string[] | null;
  to?: string | null;
  from?: string | null;
  over?: string | null;
  agg?: string | null;
  field?: string | null;
  where?: Record<string, string> | null;
};

/** One quick-create form field derived from a `{{arg}}` skeleton placeholder. */
export type EntityFormField = {
  name: string;
  widget: string;
  required: boolean;
  values?: string[] | null;
};

/** One entity type in the item's catalog — its schema + quick-create form. */
export type EntityType = {
  name: string;
  records_path: string;
  fields: EntityFieldSpec[];
  form: EntityFormField[];
};

export type EntityCatalog = {
  types: EntityType[];
  diagnostics: EntityDiagnostic[];
};

/** A projected record — raw frontmatter fields (plus resolved backref/rollup),
 * body, and per-record diagnostics. */
export type EntityInstance = {
  number: number;
  type_name: string;
  fields: Record<string, unknown>;
  body: string;
  diagnostics: EntityDiagnostic[];
};

export type EntityList = {
  entities: EntityInstance[];
  invalid: EntityInstance[];
};

const base = (slug: string, itemId: string) =>
  `/a/${enc(slug)}/items/${enc(itemId)}/entities`;

export const entitiesApi = {
  async catalog(slug: string, itemId: string): Promise<EntityCatalog> {
    const resp = await apiFetch(base(slug, itemId));
    if (!resp.ok) throw new Error(`entity catalog failed: ${resp.status}`);
    return resp.json();
  },

  async list(slug: string, itemId: string, type: string): Promise<EntityList> {
    const resp = await apiFetch(`${base(slug, itemId)}/${enc(type)}`);
    if (!resp.ok) throw new Error(`entity list failed: ${resp.status}`);
    return resp.json();
  },

  async create(
    slug: string,
    itemId: string,
    type: string,
    args: Record<string, unknown>,
  ): Promise<EntityInstance> {
    const resp = await apiFetch(`${base(slug, itemId)}/${enc(type)}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ args }),
    });
    if (!resp.ok) throw new Error(`entity create failed: ${resp.status}`);
    return resp.json();
  },

  async update(
    slug: string,
    itemId: string,
    type: string,
    number: number,
    patch: Record<string, unknown>,
  ): Promise<EntityInstance> {
    const resp = await apiFetch(`${base(slug, itemId)}/${enc(type)}/${number}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ patch }),
    });
    if (!resp.ok) throw new Error(`entity update failed: ${resp.status}`);
    return resp.json();
  },
};

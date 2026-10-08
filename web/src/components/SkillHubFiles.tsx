/**
 * A skill's files (plan-skill-hub-ux-redo D12): one line that sums them up —
 * how many, how big, how many scripts (npm gives a package's file count and
 * size, not its list) — then the files as a tree whose folders start closed
 * (GitHub's repository tree), and the text of the one opened. A list of 985
 * pills was 15,204px tall and none of them could be opened; a closed tree is
 * a few rows whatever the count.
 *
 * Folders are disclosure buttons (WAI-ARIA APG "Disclosure"), not an ARIA
 * tree: a `role="tree"` promises arrow-key navigation this does not offer.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { qk } from "../api/queryKeys";
import type { SkillHubApi, SkillHubFile } from "../api/skillHub";
import { formatBytes } from "../lib/bytes";
import { useT } from "../lib/i18n";
import { Icon } from "./Icon";

type Folder = { name: string; path: string; folders: Folder[]; files: SkillHubFile[] };

/** The paths as nested folders, each level sorted folders first, then files. */
function asTree(files: SkillHubFile[]): Folder {
  const root: Folder = { name: "", path: "", folders: [], files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let at = root;
    for (const part of parts.slice(0, -1)) {
      let next = at.folders.find((d) => d.name === part);
      if (!next) {
        next = { name: part, path: at.path ? `${at.path}/${part}` : part, folders: [], files: [] };
        at.folders.push(next);
      }
      at = next;
    }
    at.files.push(f);
  }
  const sort = (d: Folder) => {
    d.folders.sort((a, b) => a.name.localeCompare(b.name));
    d.files.sort((a, b) => a.path.localeCompare(b.path));
    d.folders.forEach(sort);
  };
  sort(root);
  return root;
}

function countIn(d: Folder): number {
  return d.files.length + d.folders.reduce((n, f) => n + countIn(f), 0);
}

export function SkillHubFiles({
  entryId,
  revision,
  files,
  scripts,
  client,
}: {
  entryId: string;
  /** The version the files are of; "" when it cannot be read file by file
   * (an entry not in git yet) — the files are then listed, not opened. */
  revision: string;
  files: SkillHubFile[];
  scripts: number;
  client: SkillHubApi;
}) {
  const t = useT();
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [shown, setShown] = useState<string | null>(null);
  const total = files.reduce((n, f) => n + (f.size ?? 0), 0);
  const tree = asTree(files);
  const toggle = (path: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const level = (d: Folder) => (
    <>
      {d.folders.map((sub) => {
        const expanded = open.has(sub.path);
        return (
          <li key={`d:${sub.path}`} className="skill-hub-tree-folder">
            <button
              type="button"
              className="skill-hub-tree-row"
              aria-expanded={expanded}
              onClick={() => toggle(sub.path)}
            >
              <Icon name={expanded ? "chev_d" : "chev_r"} size={12} />
              <span>{sub.name}/</span>
              <span className="muted small">{countIn(sub)}</span>
            </button>
            {expanded ? <ul className="skill-hub-tree">{level(sub)}</ul> : null}
          </li>
        );
      })}
      {d.files.map((f) => {
        const name = f.path.slice(f.path.lastIndexOf("/") + 1);
        const size = f.size === null ? null : formatBytes(f.size);
        return (
          <li key={`f:${f.path}`}>
            {revision ? (
              <button
                type="button"
                className="skill-hub-tree-row"
                aria-pressed={shown === f.path}
                onClick={() => setShown(f.path)}
              >
                <span className="skill-hub-tree-name">{name}</span>
                {size ? <span className="muted small">{size}</span> : null}
              </button>
            ) : (
              <span className="skill-hub-tree-row">
                <span className="skill-hub-tree-name">{name}</span>
                {size ? <span className="muted small">{size}</span> : null}
              </span>
            )}
          </li>
        );
      })}
    </>
  );

  return (
    <div className="skill-hub-files-tab">
      <p className="skill-hub-files-summary">
        {/* A size we do not know is not 0 B: an entry not in git yet, or an
            older API pod mid-rollout, gives names only. */}
        <span>
          {files.some((f) => f.size === null)
            ? t("skillHub.files.count", { count: files.length })
            : t("skillHub.files.summary", { count: files.length, size: formatBytes(total) })}
        </span>
        {scripts > 0 ? <span>{t("skillHub.files.scripts", { count: scripts })}</span> : null}
      </p>
      <div className="skill-hub-files-body">
        <ul className="skill-hub-tree" aria-label={t("skillHub.files")}>
          {level(tree)}
        </ul>
        {shown && revision ? (
          <FileText entryId={entryId} revision={revision} path={shown} client={client} />
        ) : null}
      </div>
    </div>
  );
}

function FileText({
  entryId,
  revision,
  path,
  client,
}: {
  entryId: string;
  revision: string;
  path: string;
  client: SkillHubApi;
}) {
  const t = useT();
  const file = useQuery({
    queryKey: qk.skillHubVersionFile(entryId, revision, path),
    queryFn: () => client.versionFile(entryId, revision, path),
  });
  return (
    <section className="skill-hub-file-view" aria-label={path}>
      <h3>
        <code>{path}</code>
      </h3>
      {file.isPending ? (
        <p className="muted">{t("skillHub.loading")}</p>
      ) : file.isError ? (
        <p className="error" role="alert">
          {t("skillHub.error")}
        </p>
      ) : file.data.text === null ? (
        <p className="muted">{t("skillHub.files.notText")}</p>
      ) : (
        <pre>{file.data.text}</pre>
      )}
    </section>
  );
}

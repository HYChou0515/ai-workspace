/**
 * One skill as a row of a list (plan-skill-hub-ux-redo D1): the skill hub
 * page's list and a skill's forks tab draw the same row.
 */

import { Link } from "react-router-dom";

import type { SkillHubCard } from "../api/skillHub";
import { useUser } from "../hooks/useUsers";
import { ymd } from "../lib/date";
import { useT } from "../lib/i18n";
import { countsText } from "../lib/skillHubCounts";

/** 「fork 自 Alice Wu 的 triage」: the original's owner as people know them,
 * as the owner column says it — not their id beside a name. */
export function ForkOf({ owner, name }: { owner: string; name: string }) {
  const t = useT();
  return <>{t("skillHub.forkOf.named", { owner: useUser(owner).name, name })}</>;
}

/** One skill: the whole row opens its page (the title's link is stretched
 * over it, D15); the owner and the fork count are links of their own above
 * that, so each does what it looks like it does. */
export function SkillHubRow({
  entry,
  onOwner,
  ownerHref,
}: {
  entry: SkillHubCard;
  /** Filter the list to this owner; without it the owner is plain text. */
  onOwner?: (owner: string) => void;
  /** Where that filter lives in the address — the list's other filters kept
   * — for a ctrl/cmd/middle click, which the browser opens on its own. */
  ownerHref?: (owner: string) => string;
}) {
  const t = useT();
  const ownerName = useUser(entry.owner).name;
  const href = `/skill-hub/${encodeURIComponent(entry.id)}`;
  const counts = countsText(t, entry.installs, entry.uses);
  return (
    <li className="skill-hub-row" data-testid={`entry-${entry.id}`}>
      <div className="skill-hub-row-main">
        <Link to={href} className="skill-hub-row-title">
          {entry.name}
        </Link>
        {entry.forked_from ? (
          <span className="skill-hub-row-origin">
            {entry.origin ? <ForkOf {...entry.origin} /> : t("skillHub.forkOf.gone")}
          </span>
        ) : null}
        <p className="skill-hub-row-desc">{entry.description}</p>
      </div>
      <div className="skill-hub-row-side">
        {onOwner ? (
          <a
            href={ownerHref ? ownerHref(entry.owner) : `?owner=${encodeURIComponent(entry.owner)}`}
            className="skill-hub-row-owner"
            aria-label={t("skillHub.owner.only", { name: ownerName })}
            title={t("skillHub.owner.only", { name: ownerName })}
            onClick={(e) => {
              // A modified click opens a new tab: the browser's, not ours.
              if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button !== 0) return;
              e.preventDefault();
              onOwner(entry.owner);
            }}
          >
            {ownerName}
          </a>
        ) : (
          <span className="skill-hub-row-owner">{ownerName}</span>
        )}
        {counts ? <span>{counts}</span> : null}
        {entry.updated_at ? (
          <span>{t("skillHub.updated", { day: ymd(entry.updated_at) })}</span>
        ) : null}
        {entry.fork_count > 0 ? (
          <Link to={`${href}?tab=forks`} className="skill-hub-row-forks">
            {entry.fork_count === 1
              ? t("skillHub.fork.one")
              : t("skillHub.forks", { count: entry.fork_count })}
          </Link>
        ) : null}
      </div>
    </li>
  );
}

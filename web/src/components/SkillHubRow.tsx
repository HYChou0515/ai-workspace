/**
 * One skill as a row of a list (plan-skill-hub-ux-redo D1): the skill hub
 * page's list and a skill's forks tab draw the same row.
 */

import { Link } from "react-router-dom";

import type { SkillHubCard } from "../api/skillHub";
import { useUser } from "../hooks/useUsers";
import { ymd } from "../lib/date";
import { useT } from "../lib/i18n";

/** One skill: the whole row opens its page (the title's link is stretched
 * over it, D15); the owner and the fork count are links of their own above
 * that, so each does what it looks like it does. */
export function SkillHubRow({
  entry,
  onOwner,
}: {
  entry: SkillHubCard;
  /** Filter the list to this owner; without it the owner is plain text. */
  onOwner?: (owner: string) => void;
}) {
  const t = useT();
  const ownerName = useUser(entry.owner).name;
  const href = `/skill-hub/${encodeURIComponent(entry.id)}`;
  const counted = entry.installs > 0 || entry.uses > 0;
  return (
    <li className="skill-hub-row" data-testid={`entry-${entry.id}`}>
      <div className="skill-hub-row-main">
        <Link to={href} className="skill-hub-row-title">
          {entry.name}
        </Link>
        {entry.forked_from ? (
          <span className="skill-hub-row-origin">
            {entry.origin
              ? t("skillHub.forkOf", { origin: `${entry.origin.owner}/${entry.origin.name}` })
              : t("skillHub.forkOf.gone")}
          </span>
        ) : null}
        <p className="skill-hub-row-desc">{entry.description}</p>
      </div>
      <div className="skill-hub-row-side">
        {onOwner ? (
          <a
            href={`?owner=${encodeURIComponent(entry.owner)}`}
            className="skill-hub-row-owner"
            aria-label={t("skillHub.owner.only", { name: ownerName })}
            title={t("skillHub.owner.only", { name: ownerName })}
            onClick={(e) => {
              e.preventDefault();
              onOwner(entry.owner);
            }}
          >
            {ownerName}
          </a>
        ) : (
          <span className="skill-hub-row-owner">{ownerName}</span>
        )}
        {counted ? (
          <span>{t("skillHub.counts", { installs: entry.installs, uses: entry.uses })}</span>
        ) : null}
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

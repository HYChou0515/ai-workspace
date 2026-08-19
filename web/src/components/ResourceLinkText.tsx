/**
 * A refusal message, rendered so the place it sends you to is REACHABLE (#692).
 *
 * The resource limits (#688) refuse outright, and that is only defensible
 * because the refused person has somewhere to go: `/my-resources`, where they
 * close an environment or find the item eating their space. Every one of those
 * messages named the page — and every one of them was flat text, on screens
 * whose only route to it was a popover in the far corner of the global nav. The
 * page existed; at the moment it was needed it was not reachable, and reachable
 * is what the "just refuse them" trade-off actually rested on.
 *
 * So: the message keeps saying what it said, and the page's OWN NAME inside it
 * becomes the link. No "click here" tacked on the end, no second sentence — the
 * remedy is already a noun in that sentence, it just needed to be pressable.
 *
 * Matching on the name rather than on a `{placeholder}` is deliberate. Both the
 * sentence and the name come from the same catalog, so they agree by
 * construction, and a caller that forgets to render through this component
 * degrades to the plain sentence it shows today — not to a leaked `{token}` in
 * the user's face. What that trades away (a reworded message silently losing its
 * link) is bought back by the guard test over {@link RESOURCE_LINK_KEYS}.
 */
import { Link } from "react-router-dom";

import { type MsgKey, useT } from "../lib/i18n";

/** Where a person goes when a resource limit refuses them. */
export const MY_RESOURCES_PATH = "/my-resources";

/**
 * Every message that promises the page. The guard test asserts each one still
 * spells its name the way the catalog does — reword one and the sentence would
 * read fine while quietly going back to being unclickable.
 *
 * The workspace-full wordings are absent on purpose: they point at THIS item's
 * files, which the user deletes right where they are, so there is nothing on
 * `/my-resources` for them to do.
 */
export const RESOURCE_LINK_KEYS = [
  "chat.send.envFull",
  "chat.send.userFull",
  "terminal.envFull",
  "terminal.userFull",
  "workspace.overQuota.user",
  "workspace.overQuota.env",
  "workspace.upload.userFull",
  "workspace.upload.envFull",
  // The "and also" clause. The workspace one is absent for the same reason its
  // primary is: its remedy is this item's own files, not anything on
  // `/my-resources`. There is no environment clause at all — that limit always
  // leads (see `QUOTA_ALSO_KEY`).
  "resources.also.user",
] as const satisfies readonly MsgKey[];

export function ResourceLinkText({ text }: { text: string }) {
  const t = useT();
  const name = t("resources.title");
  const at = text.indexOf(name);
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <Link
        to={MY_RESOURCES_PATH}
        style={{ color: "inherit", textDecoration: "underline", fontWeight: 600 }}
      >
        {name}
      </Link>
      {text.slice(at + name.length)}
    </>
  );
}

/**
 * A WUI at its own URL — `/w/{slug}/{itemId}/{path to the view file}`.
 *
 * The decision that makes this small: **whoever opens the link must already be
 * able to see the item.** So there is no new permission model, no export and no
 * second server. Same login, same file service, same assembler, same
 * sandbox-plus-CSP envelope — just without the workspace shell around it.
 *
 * The URL is a shortcut, not a grant. It gives nobody access they did not have,
 * and it takes none away: the API refuses exactly what it would have refused
 * inside the workspace. What it buys is that a colleague who is already in the
 * item does not have to go hunting through a file tree to find the page you
 * told them about.
 *
 * Rendered OUTSIDE `GlobalLayout` on purpose. A nav bar and a breadcrumb trail
 * are for navigating a workspace; someone who followed a link to one page has
 * nowhere to navigate to and no context for the crumbs.
 */
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { FileServiceProvider, investigationFileService, type FileService } from "../api/fileService";
import { classifyReadFailure } from "../renderers/wui/assets";
import { TryAgain } from "../renderers/wui/TryAgain";
import { parseViewSpec } from "../renderers/entity/EntityViews";
import { VIEW_KIND } from "../renderers/entity/types";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { WuiView } from "../renderers/wui/WuiView";
import { useQuery } from "@tanstack/react-query";

/** A sentence, centred, for the ways this URL can be wrong — and, where the
 * wrongness may be a moment's (a read that failed, a file a restoring sandbox
 * answered "not there" for), a way to look again without reloading the tab. */
function Problem({ children, retry }: { children: React.ReactNode; retry?: () => void }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        padding: 24,
        textAlign: "center",
        color: "var(--ink-2)",
      }}
    >
      {/* The SENTENCE is the alert; the button sits beside it. An `alert` is a
          live region assistive tech announces and does not expect controls
          in, so a button inside one is read as text and may not be reached. */}
      <p role="alert" style={{ maxWidth: "42rem", margin: 0 }}>
        {children}
      </p>
      {retry && <TryAgain onClick={retry} />}
    </div>
  );
}

export function WuiPage({
  makeService = investigationFileService,
}: {
  /** Seam for tests; production always builds the item's own service. */
  makeService?: (slug: string, itemId: string) => FileService;
}) {
  const { slug = "", itemId = "", "*": rest = "" } = useParams();
  const path = `/${rest}`;
  const service = useMemo(() => makeService(slug, itemId), [makeService, slug, itemId]);
  /** Bumped by the reader's Try again AFTER the view file has been re-read,
   * and keyed onto the pane: a fresh pane then reads the folder once, with
   * what the view file now says. Re-reading the folder first (with the old
   * `entry:`) and the view file second read it twice and showed "not
   * published" for the moment in between. */
  const [attempt, setAttempt] = useState(0);

  const view = useQuery({
    queryKey: ["wui-page", slug, itemId, path],
    queryFn: async () => {
      const file = await service.readFile(path);
      return file.kind === "text" ? file.text : "";
    },
    retry: false,
  });

  // Also what a press on Try again shows: a refetch of a query that never
  // had data goes back through `pending`, so the sentence is replaced while
  // the read is in flight and a second failure is visibly a second one.
  // (Pinned by a test — a version of TanStack that kept `error` while
  // refetching would leave the button looking dead.)
  if (view.isPending) return <Problem>Opening {path}…</Problem>;
  if (view.isError) {
    // Named, because the reader did not choose this path — somebody sent them
    // the link, and the path is the only thing they can forward back. What
    // the failure MEANS is decided once, in `classifyReadFailure`, for every
    // read a WUI makes — a 403 is a member without this right (an outsider
    // gets 404: the item's existence is not theirs to probe), a dropped
    // connection is not absence — so this sentence and the one the pane
    // shows for the entry cannot disagree. And "not there" is tentative,
    // with a way to look again: on this platform a read during a sandbox
    // restore answers 404 for a file that is there, and a reader told "there
    // is no file" in the indicative reported one to an author who could see
    // it.
    const why = classifyReadFailure(view.error, path);
    const retry = () => void view.refetch();
    if (why.kind === "failed") {
      // A permanent failure (a 403 — the reader, not the moment) offers no
      // Try again: the same sentence every press hides the only fix.
      return <Problem retry={why.permanent ? undefined : retry}>{why.reason}</Problem>;
    }
    // "Not there" names the access case too: the backend answers 404, not
    // 403, to a reader who may not see the item at all — the colleague the
    // author forgot to add — and a sentence about a missing file sent them
    // to report one.
    return (
      <Problem retry={retry}>
        There is no file at {path} in this item, or you may not have access to this item — or it is
        still being restored. Try again in a moment.
      </Problem>
    );
  }

  const spec = parseViewSpec(view.data ?? "");
  if (!spec || spec.view !== VIEW_KIND.wui) {
    // Not a general file viewer. An empty frame would read as a broken page
    // rather than as a wrong link.
    return <Problem>{path} is not a page. This address only opens a WUI.</Problem>;
  }

  return (
    // The slug comes from a CONTEXT, not from the route params, and `WuiView`
    // reads it to call tools and start workflows — the one thing a reader's
    // page keeps (it never builds here, by design: `chrome="viewer"`). Without
    // this provider `callTool` would be null and every tool button would do
    // nothing, without a word — which is why it is provided rather than
    // relied on.
    <WorkspaceSlugProvider value={slug}>
      <FileServiceProvider value={service}>
        <div style={{ position: "fixed", inset: 0, display: "flex", flexDirection: "column" }}>
          {/* The reader's chrome: no toolbar, no build log, no reports, and the
              page is never rebuilt on their account — a link serves what is
              already built (docs/plan-wui-deploy.md). */}
          <WuiView
            key={attempt}
            path={path}
            spec={spec}
            chrome="viewer"
            onRetry={() => void view.refetch().then(() => setAttempt((n) => n + 1))}
          />
        </div>
      </FileServiceProvider>
    </WorkspaceSlugProvider>
  );
}

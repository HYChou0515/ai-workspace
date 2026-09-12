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
import { useMemo } from "react";
import { useParams } from "react-router-dom";

import { FileServiceProvider, investigationFileService, type FileService } from "../api/fileService";
import { HttpError } from "../api/http";
import { parseViewSpec } from "../renderers/entity/EntityViews";
import { VIEW_KIND } from "../renderers/entity/types";
import { WorkspaceSlugProvider } from "../hooks/useWorkspaceSlug";
import { WuiView } from "../renderers/wui/WuiView";
import { useQuery } from "@tanstack/react-query";

/** A sentence, centred, for the two ways this URL can be wrong. */
function Problem({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        padding: 24,
        textAlign: "center",
        color: "var(--ink-2)",
      }}
    >
      <p style={{ maxWidth: "42rem" }}>{children}</p>
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

  const view = useQuery({
    queryKey: ["wui-page", slug, itemId, path],
    queryFn: async () => {
      const file = await service.readFile(path);
      return file.kind === "text" ? file.text : "";
    },
    retry: false,
  });

  if (view.isPending) return <Problem>Opening {path}…</Problem>;
  if (view.isError) {
    // Named, because the reader did not choose this path — somebody sent them
    // the link, and the path is the only thing they can forward back. But
    // only "not there" is "no file": a 403 is somebody outside the item, and
    // a dropped connection is neither — both used to read as a missing file,
    // and the reader reported one to an author who could see it. The same
    // classes `readAsset` (assets.ts) draws one level down, so the two
    // sentences a reader can meet on this route agree.
    const err = view.error;
    if (err instanceof HttpError && err.status === 403) {
      return <Problem>You cannot open this item, so {path} cannot be shown.</Problem>;
    }
    if (err instanceof HttpError && err.status !== 404) {
      return (
        <Problem>
          {path} could not be read (the workspace answered {err.status}). Try again in a moment.
        </Problem>
      );
    }
    if (err instanceof TypeError) {
      return <Problem>Could not reach the workspace to read {path}. Try again in a moment.</Problem>;
    }
    return <Problem>There is no file at {path} in this item.</Problem>;
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
          <WuiView path={path} spec={spec} chrome="viewer" />
        </div>
      </FileServiceProvider>
    </WorkspaceSlugProvider>
  );
}

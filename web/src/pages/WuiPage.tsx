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
    // the link, and the path is the only thing they can forward back.
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
    // reads it to build and to call tools. Without this provider both would go
    // quietly missing here: auto-rebuild would never fire (so the page shows a
    // stale `dist/`) and `callTool` would be null (so every tool button does
    // nothing). Neither says anything, which is why it is provided rather than
    // relied on.
    <WorkspaceSlugProvider value={slug}>
      <FileServiceProvider value={service}>
        <div style={{ position: "fixed", inset: 0, display: "flex", flexDirection: "column" }}>
          <WuiView path={path} spec={spec} />
        </div>
      </FileServiceProvider>
    </WorkspaceSlugProvider>
  );
}

/**
 * The "preparing" page — `/open/{slug}/{itemId}/{path}` (C12 of
 * `docs/plan-wui-publish-and-agent-hub.md`).
 *
 * The address a published WUI or agent hands out. Nobody works in the source,
 * so following it asks the server for the opener's own copy (`POST
 * /published/open`: made now, or the one they already have), then replaces
 * this page with the copy — a WUI's page, or an agent's conversation.
 *
 * Outside the shell for the same reason as a WUI at its own URL: whoever
 * followed a link has one thing to wait for, not a workspace to navigate.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { HttpError } from "../api/http";
import { publishApi, type PublishApi } from "../api/publish";
import { qk } from "../api/queryKeys";
import { encodePath } from "../api/refPath";
import { useT } from "../lib/i18n";

/** Where a copy is used: a WUI at its own URL; an agent in its item, whose
 * landing is the conversation (A10). SPA routes — the router adds the base. */
export function copyRoute(kind: "wui" | "agent", slug: string, itemId: string, path: string): string {
  const base = `/${encodeURIComponent(slug)}/${encodeURIComponent(itemId)}`;
  return kind === "wui" ? `/w${base}/${encodePath(path)}` : `/a${base}`;
}

export function OpenPublishedPage({ client = publishApi }: { client?: Pick<PublishApi, "open"> }) {
  const t = useT();
  const navigate = useNavigate();
  // The slug in the address is for a reader; the copy names its own App.
  const { itemId = "", "*": rest = "" } = useParams();
  const path = `/${rest}`;

  // A query, not an effect: one open per address however often the page
  // mounts (StrictMode mounts twice, and a "fresh" sharing would otherwise
  // hand the opener two copies). Never retried on its own — a second try of
  // an open that made a copy and then lost the answer would make another.
  const opened = useQuery({
    queryKey: ["published-open", itemId, path],
    queryFn: () => client.open(itemId, path),
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
  });

  const qc = useQueryClient();
  const data = opened.data;
  useEffect(() => {
    if (!data) return;
    // An open counts (opens, people) and may make a conversation the agent's
    // page lists — so the lists that show those read again, wherever the
    // person goes next.
    void qc.invalidateQueries({ queryKey: qk.agentHub });
    void qc.invalidateQueries({ queryKey: qk.wuiOverview });
    navigate(copyRoute(data.kind, data.slug, data.item_id, path), { replace: true });
  }, [data, navigate, path, qc]);

  const err = opened.error;
  const sentence = !err
    ? null
    : err instanceof HttpError && err.status === 404
      ? t("open.notFound")
      : err instanceof HttpError && err.status === 410
        ? t("open.gone")
        : t("open.failed", { why: err.message });

  return (
    <div
      data-testid="open-published"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        padding: 24,
        textAlign: "center",
        color: "var(--ink-2)",
      }}
    >
      {sentence ? (
        <p role="alert" style={{ maxWidth: "42rem", margin: 0 }}>
          {sentence}
        </p>
      ) : (
        <>
          <p role="status" style={{ margin: 0, color: "var(--ink)" }}>
            {t("open.preparing")}
          </p>
          <p style={{ margin: 0 }}>{t("open.preparing.hint")}</p>
        </>
      )}
    </div>
  );
}

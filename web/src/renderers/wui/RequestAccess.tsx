/**
 * 申請存取 for an item, through #968's request flow (`POST …/request-access`,
 * deduplicated server-side) — shared by the reader's 403 page (`WuiPage`) and
 * the WUI User banner (`WuiUserNeeded`, docs/plan-wui-user.md D7).
 *
 * A caller who can already ENTER the workspace is told so by `already_readable`
 * and the route notifies nobody; `inside` is what to say then, which differs by
 * what the person lacks. One press; a request that did not go through says so
 * and may be sent again (NN/g, "Error-Message Guidelines": offer a way forward).
 */
import { useState } from "react";

import { api } from "../../api";
import { Btn } from "../../components/Btn";
import { useT, type MsgKey } from "../../lib/i18n";

export function RequestAccess({
  slug,
  itemId,
  inside,
  insideVars,
}: {
  slug: string;
  itemId: string;
  inside: MsgKey;
  insideVars?: Record<string, string>;
}) {
  const t = useT();
  const [state, setState] = useState<"idle" | "sending" | "sent" | "inside" | "failed">("idle");
  const ask = async () => {
    setState("sending");
    try {
      const answer = await api.requestItemAccess(slug, itemId);
      // `requested: false` alone is also a repeat of a request already with the
      // owner (the route's dedup ledger) — still "requested". Only
      // `already_readable` means nobody was asked (#968, review round 2).
      setState(answer.already_readable ? "inside" : "sent");
    } catch {
      setState("failed");
    }
  };
  if (state === "inside") return <p role="status">{t(inside, insideVars)}</p>;
  return (
    <>
      <Btn size="sm" disabled={state === "sending" || state === "sent"} onClick={() => void ask()}>
        {t(state === "sent" ? "entry.withheld.requested" : "entry.withheld.requestAccess")}
      </Btn>
      {state === "failed" && <p role="alert">{t("wui.page.requestFailed")}</p>}
    </>
  );
}

/**
 * Pager (#506 G2) — a prev/next pager for the review inbox's server-paginated
 * views. The backend caps each fetch to `pageSize` rows and reports the full
 * filtered `total`, so the FE renders one page instead of thousands (the ① fix).
 * The prev/next nav only appears when there's more than one page; the total count
 * always shows so the reviewer knows the backlog size.
 */
import { Btn } from "../../components/Btn";
import { useT } from "../../lib/i18n";

export function Pager({
  total,
  offset,
  pageSize,
  onOffset,
}: {
  total: number;
  offset: number;
  pageSize: number;
  onOffset: (offset: number) => void;
}) {
  const t = useT();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(pages, Math.floor(offset / pageSize) + 1);
  return (
    <div className="rvw-pager" role="navigation" aria-label={t("review.pager.label")}>
      <span className="rvw-pager__total">{t("review.pager.total", { n: total })}</span>
      {pages > 1 && (
        <div className="rvw-pager__nav">
          <Btn
            size="sm"
            variant="ghost"
            disabled={offset <= 0}
            onClick={() => onOffset(Math.max(0, offset - pageSize))}
          >
            {t("review.pager.prev")}
          </Btn>
          <span className="rvw-pager__pos">{t("review.pager.pos", { page, pages })}</span>
          <Btn
            size="sm"
            variant="ghost"
            disabled={offset + pageSize >= total}
            onClick={() => onOffset(offset + pageSize)}
          >
            {t("review.pager.next")}
          </Btn>
        </div>
      )}
    </div>
  );
}

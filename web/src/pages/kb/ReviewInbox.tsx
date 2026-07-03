/**
 * ReviewInbox — the shared "待審核" surface shell (#415). It owns only the chrome:
 * a titled panel, a loading skeleton, an empty state, and the list container. The
 * rows are type-specific (a card-gen run, a #377 clarification question) and
 * passed as children, so the SAME shell serves a collection-scoped tab and — at
 * global scope — the cross-collection inbox (#377 refactor, P5).
 */
import type { ReactNode } from "react";

import { Skeleton } from "../../components/Skeleton";

export function ReviewInbox({
  title,
  subtitle,
  isLoading,
  isEmpty,
  emptyText,
  children,
}: {
  title: string;
  subtitle?: string;
  isLoading: boolean;
  isEmpty: boolean;
  emptyText: string;
  children: ReactNode;
}) {
  return (
    <div className="kb-review">
      <header className="kb-review__head">
        <h2 className="kb-review__title">{title}</h2>
        {subtitle && <p className="kb-review__sub">{subtitle}</p>}
      </header>
      {isLoading ? (
        <div data-testid="review-loading">
          <Skeleton style={{ height: 72 }} />
        </div>
      ) : isEmpty ? (
        <div className="kb-review__empty">{emptyText}</div>
      ) : (
        <ul className="kb-review__list">{children}</ul>
      )}
    </div>
  );
}

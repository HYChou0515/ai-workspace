// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { NotificationItem } from "../api/types";
import { makeTestQueryClient, QueryWrap } from "../test/queryWrapper";
import { useNotifications } from "./useNotifications";

afterEach(() => vi.restoreAllMocks());

const N = (over: Partial<NotificationItem>): NotificationItem => ({
  resource_id: "n",
  kind: "status",
  title: "t",
  body: "",
  link: "/x",
  actor: null,
  read: false,
  created_at: 1,
  ...over,
});

describe("useNotifications", () => {
  it("counts unread and fires mark-all-read", async () => {
    vi.spyOn(api, "getNotifications").mockResolvedValue([
      N({ resource_id: "n1", read: false }),
      N({ resource_id: "n2", read: true }),
    ]);
    const markAll = vi.spyOn(api, "markAllNotificationsRead").mockResolvedValue();
    const client = makeTestQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryWrap client={client}>{children}</QueryWrap>
    );

    const { result } = renderHook(() => useNotifications(), { wrapper });
    await waitFor(() => expect(result.current.items.length).toBe(2));
    expect(result.current.unread).toBe(1);

    result.current.markAllRead();
    await waitFor(() => expect(markAll).toHaveBeenCalled());
  });
});

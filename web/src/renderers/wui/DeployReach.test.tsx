// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { qk } from "../../api/queryKeys";
import type { AppItem, AppManifest } from "../../api/types";
import { makeTestQueryClient, QueryWrap } from "../../test/queryWrapper";
import { DeployReach } from "./DeployReach";

// `docs/plan-deploy-reach.md` decisions 2, 4, 5: after Deploy the panel says
// who the link opens for NOW, and offers the share dialog to whoever may change
// it — or names the owner to ask.

const SLUG = "rca";
const ID = "item1";
const MANIFEST = {
  slug: SLUG,
  resource_route: "/rca-investigation",
  labels: {},
  item: { noun: "Investigation" },
  layout: { primary_surface: "ide" },
} as unknown as AppManifest;

function setup({
  me = "bob",
  meKnown = true,
  superuser = false,
  permission,
  owner = "bob",
  manifest = MANIFEST,
  item = true,
}: {
  me?: string;
  meKnown?: boolean;
  superuser?: boolean;
  permission?: unknown;
  owner?: string;
  manifest?: AppManifest;
  item?: boolean;
}) {
  const client = makeTestQueryClient();
  // What is seeded is what is read: no query here may refetch on mount and
  // replace it with whatever an unstubbed `fetch` answers.
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, staleTime: Infinity } });
  if (meKnown) client.setQueryData(qk.currentUser, me);
  client.setQueryData(qk.me, { is_superuser: superuser, groups: [] });
  client.setQueryData(qk.appManifest(SLUG), manifest);
  client.setQueryData(qk.users, []);
  if (item) {
    client.setQueryData(qk.appItem(SLUG, ID), {
      resource_id: ID,
      title: "Line 3",
      created_by: owner,
      owner,
      ...(permission === undefined ? {} : { permission }),
    } as unknown as AppItem);
  }
  render(
    <QueryWrap client={client}>
      <DeployReach slug={SLUG} itemId={ID} />
    </QueryWrap>,
  );
  return client;
}

afterEach(cleanup);

describe("DeployReach", () => {
  it("a private workspace's owner: only you — and the share dialog right here", () => {
    setup({ permission: { visibility: "private" } });

    expect(screen.getByText("目前只有你打得開這個連結。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "分享設定…" }));
    expect(screen.getByTestId("item-share-dialog")).toBeInTheDocument();
  });

  it("an admin deploying someone else's private workspace: the owner and admins", () => {
    setup({ me: "root", superuser: true, owner: "bob", permission: { visibility: "private" } });

    expect(screen.getByText("目前只有擁有者和管理員打得開這個連結。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "分享設定…" })).toBeInTheDocument();
  });

  it("shared with members: only the members", () => {
    setup({
      permission: { visibility: "restricted", read_meta: ["user:a"], read_content: ["user:a"] },
    });

    expect(screen.getByText("只有這個 workspace 的成員打得開這個連結。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "分享設定…" })).toBeInTheDocument();
  });

  it("open to everyone: says so, and offers nothing to fix", () => {
    setup({ permission: { visibility: "public" } });

    expect(screen.getByText("所有人都打得開這個連結。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享設定…" })).toBeNull();
  });

  it("a collaborator who may not change access: no button — the owner to ask, by name", () => {
    setup({
      me: "carol",
      owner: "bob",
      permission: {
        visibility: "restricted",
        read_meta: ["user:carol"],
        read_content: ["user:carol"],
        edit_content: ["user:carol"],
      },
    });

    expect(screen.getByText("只有這個 workspace 的成員打得開這個連結。")).toBeInTheDocument();
    expect(screen.getByText("要給其他人看，請 workspace 的擁有者 bob 把他加進來。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享設定…" })).toBeNull();
  });

  it("a chat-first App opens its own share dialog, as its top bar does", () => {
    setup({
      permission: { visibility: "private" },
      manifest: { ...MANIFEST, layout: { primary_surface: "chat" } } as unknown as AppManifest,
    });

    fireEvent.click(screen.getByRole("button", { name: "分享設定…" }));
    expect(screen.getByRole("dialog", { name: "Share chat" })).toBeInTheDocument();
  });

  it("follows a change of access — it re-renders when the workspace's own query changes", async () => {
    const client = setup({ permission: { visibility: "private" } });
    expect(screen.getByText("目前只有你打得開這個連結。")).toBeInTheDocument();

    await act(async () => {
      client.setQueryData(qk.appItem(SLUG, ID), (old: AppItem | undefined) => ({
        ...(old as AppItem),
        permission: { visibility: "public" },
      }));
    });

    expect(await screen.findByText("所有人都打得開這個連結。")).toBeInTheDocument();
  });

  it("before the viewer is known: the rule — not 'the owner and admins' said to the owner", () => {
    // Review round 1: until the identity settles the id is a placeholder, so
    // the owner read the non-owner's sentence (and, without the right to
    // change access, was told to ask themselves).
    setup({ meKnown: false, permission: { visibility: "private" } });
    expect(screen.getByText("能讀這個 workspace 檔案的人才能用這個連結。")).toBeInTheDocument();
    expect(screen.queryByText(/擁有者/)).toBeNull();
  });

  it("before the workspace is read, or when its access cannot be read: the rule, never a guess", () => {
    setup({ item: false });
    expect(screen.getByText("能讀這個 workspace 檔案的人才能用這個連結。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享設定…" })).toBeNull();
    cleanup();

    setup({ permission: { visibility: "secret" } });
    expect(screen.getByText("能讀這個 workspace 檔案的人才能用這個連結。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享設定…" })).toBeNull();
  });
});

// @vitest-environment happy-dom
/**
 * #fe-responsive — the shell picks its layout mode from ITS OWN width.
 *
 * It used to ask the viewport (`useIsNarrow()`), which is only the same number
 * when the shell fills the window. A chat-first App does not: `AppWorkspace`
 * puts a 240px `ChatListRail` beside it. Measured in a real browser at a 768px
 * viewport, the shell had 528px, still reported "wide", and laid out activity
 * bar (50) + file tree (260) + editor (min 360) + chat (380) into it. Every
 * column past 528px was silently clipped by `page-item`'s `overflow: hidden` —
 * the editor's empty state rendered underneath the chat, the composer's model
 * picker was cut in half, and the whole surface was unusable.
 */
import "@testing-library/jest-dom/vitest";
import { act, cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppItem, AppManifest } from "../../api/types";
import { renderWithQuery } from "../../test/queryWrapper";
import { WorkspaceShell } from "./WorkspaceShell";

vi.mock("../../components/ItemChatShell", () => ({ ItemChatShell: () => null }));
vi.mock("../../components/PresenceBar", () => ({ PresenceBar: () => null }));
vi.mock("../../components/ActivityFeed", () => ({ ActivityFeed: () => null }));
vi.mock("../../hooks/useAgent", () => ({
  AgentProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAgent: () => ({ log: { entries: [], streaming: false }, metrics: null }),
}));
vi.mock("../../hooks/useIsSuperuser", () => ({
  useIsSuperuser: () => true,
  useIsSuperuserState: () => ({ isSuperuser: true, ready: true, groups: [] }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => "root",
  useCurrentUserState: () => ({ id: "root", ready: true }),
}));

const manifest = {
  slug: "playground",
  title: "Playground",
  icon: "sparkle",
  color: "#000",
  function: { workspace: true, sandbox: false, terminal: false },
  agent: { picker: [] },
  item: { noun: "Scratch", noun_plural: "Scratches" },
  layout: {
    breadcrumb: [],
    statusbar: [],
    list: [],
    default_tabs: [],
    // IDE-first, so the file columns are actually mounted and the mode matters.
    primary_surface: "ide",
    chat_switcher: false,
  },
  labels: {},
  fields: [],
  field_styles: {},
  profiles: [],
  default_profile: "default",
  resource_route: "/playground-item",
} as unknown as AppManifest;

const item = {
  resource_id: "PG-1",
  title: "Sine wave demo",
  owner: "root",
  created_by: "root",
  permission: { visibility: "private" },
} as unknown as AppItem;

const realRO = globalThis.ResizeObserver;
const realMM = window.matchMedia;
let emit: (width: number) => void;

beforeEach(() => {
  const callbacks: ResizeObserverCallback[] = [];
  globalThis.ResizeObserver = class {
    constructor(cb: ResizeObserverCallback) {
      callbacks.push(cb);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  emit = (width) => {
    for (const cb of callbacks) {
      cb([{ contentRect: { width } } as unknown as ResizeObserverEntry], {} as ResizeObserver);
    }
  };
  // The viewport insists it is WIDE for every test here — that is the whole
  // point: the shell must not take its answer from the window.
  window.matchMedia = ((q: string) => ({
    matches: false,
    media: q,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => true,
  })) as unknown as typeof window.matchMedia;
});

afterEach(() => {
  globalThis.ResizeObserver = realRO;
  window.matchMedia = realMM;
  localStorage.clear();
  cleanup();
});

function open() {
  return renderWithQuery(
    <MemoryRouter>
      <WorkspaceShell manifest={manifest} item={item} files={[{ path: "/notes.md", size: 9 }]} />
    </MemoryRouter>,
  );
}

describe("WorkspaceShell layout mode follows its own width", () => {
  it("goes narrow when the shell's box is narrow, even though the viewport says wide", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("page-item")).toBeInTheDocument());
    // 768px viewport minus a chat-first App's 240px rail.
    act(() => emit(528));
    await waitFor(() =>
      expect(screen.getByTestId("page-item")).toHaveAttribute("data-narrow", "true"),
    );
  });

  it("stays wide when the shell's box is wide", async () => {
    open();
    await waitFor(() => expect(screen.getByTestId("page-item")).toBeInTheDocument());
    act(() => emit(1280));
    await waitFor(() =>
      expect(screen.getByTestId("page-item")).toHaveAttribute("data-narrow", "false"),
    );
  });

  it("re-evaluates on every resize, not only when the 767px media query flips", async () => {
    // `window.innerWidth` was read during render with nothing subscribed to
    // `resize`, so any change that did not cross the media boundary left the
    // shell's width maths stale. An observer sees every step.
    open();
    await waitFor(() => expect(screen.getByTestId("page-item")).toBeInTheDocument());
    act(() => emit(1280));
    await waitFor(() =>
      expect(screen.getByTestId("page-item")).toHaveAttribute("data-narrow", "false"),
    );
    act(() => emit(600));
    await waitFor(() =>
      expect(screen.getByTestId("page-item")).toHaveAttribute("data-narrow", "true"),
    );
    act(() => emit(1100));
    await waitFor(() =>
      expect(screen.getByTestId("page-item")).toHaveAttribute("data-narrow", "false"),
    );
  });
});

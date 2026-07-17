// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KbApi, KbCollection } from "../../api/kb";
import { mockKbApi } from "../../api/kbMock";
import { QueryWrap } from "../../test/queryWrapper";
import { CardGenToggle } from "./CardGenToggle";

const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: QueryWrap });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const coll = (over: Partial<KbCollection>): KbCollection => ({
  resource_id: "c1",
  name: "C1",
  description: "",
  icon: "layers",
  cited: 0,
  doc_count: 0,
  size: 0,
  tokens: 0,
  updated_at: 0,
  owner: "u",
  use_rag: true,
  use_wiki: false,
  wiki_maintainer_guidance: "",
  wiki_reader_guidance: "",
  is_global: false,
  auto_digest: false,
  ...over,
});

function client(over: Partial<KbApi> = {}): KbApi {
  return { ...mockKbApi, ...over };
}

describe("CardGenToggle", () => {
  it("reflects the collection's auto_digest state", () => {
    render(<CardGenToggle collection={coll({ auto_digest: true })} client={client()} />);
    expect(screen.getByTestId("kb-autodigest-toggle")).toBeChecked();
  });

  it("is unchecked when auto_digest is off", () => {
    render(<CardGenToggle collection={coll({ auto_digest: false })} client={client()} />);
    expect(screen.getByTestId("kb-autodigest-toggle")).not.toBeChecked();
  });

  it("persists the choice via updateCollection on change", async () => {
    const updateCollection = vi.fn(async () => {});
    render(
      <CardGenToggle
        collection={coll({ resource_id: "c9", auto_digest: false })}
        client={client({ updateCollection })}
      />,
    );
    fireEvent.click(screen.getByTestId("kb-autodigest-toggle"));
    await waitFor(() =>
      expect(updateCollection).toHaveBeenCalledWith("c9", { auto_digest: true }),
    );
  });
});

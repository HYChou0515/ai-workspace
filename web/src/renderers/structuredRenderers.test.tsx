// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EditModeProvider } from "../hooks/editMode";
import { FileBufferProvider, FileBufferStore } from "../hooks/fileBuffer";
import { JsonRenderer, JsonlRenderer, YamlRenderer } from "./structuredRenderers";

function storeWith(text: string, path: string): FileBufferStore {
  return new FileBufferStore({
    readFile: vi.fn(async () => ({
      kind: "text" as const,
      path,
      size: text.length,
      text,
      encoding: "utf-8" as const,
    })),
    writeFile: vi.fn(async () => {}),
  });
}

async function renderLoaded(node: React.ReactElement, store: FileBufferStore, path: string) {
  store.ensureLoaded(path);
  await new Promise((r) => setTimeout(r, 0));
  return render(
    <EditModeProvider>
      <FileBufferProvider store={store}>{node}</FileBufferProvider>
    </EditModeProvider>,
  );
}

describe("structured renderers read the buffer and render the core", () => {
  afterEach(cleanup);

  it("JsonRenderer renders a .json buffer as a tree", async () => {
    const path = "/data/x.json";
    await renderLoaded(<JsonRenderer path={path} />, storeWith('{"name": "widget"}', path), path);
    expect(await screen.findByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/widget/)).toBeInTheDocument();
  });

  it("JsonlRenderer renders one card per line", async () => {
    const path = "/data/x.jsonl";
    await renderLoaded(<JsonlRenderer path={path} />, storeWith('{"a": 1}\n{"b": 2}\n', path), path);
    expect(await screen.findAllByTestId("jsonl-record")).toHaveLength(2);
  });

  it("YamlRenderer renders a .yaml buffer as a tree", async () => {
    const path = "/data/x.yaml";
    await renderLoaded(<YamlRenderer path={path} />, storeWith("name: widget\n", path), path);
    expect(await screen.findByText(/name/)).toBeInTheDocument();
    expect(screen.getByText(/widget/)).toBeInTheDocument();
  });
});

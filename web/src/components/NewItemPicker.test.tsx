// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowManifestDTO } from "../api/workflows";
import { NewItemPicker } from "./NewItemPicker";

afterEach(cleanup);

const WORKFLOWS: WorkflowManifestDTO[] = [
  { id: "memory", title: "Digest into memory", phases: [], input_json: "x", description: "d1" },
  { id: "collections", title: "File into collections", phases: [], input_json: "x" },
];

describe("NewItemPicker", () => {
  it("labels the trigger so it reads as opening a create menu, not a bare adjective (#466)", () => {
    render(<NewItemPicker workflows={WORKFLOWS} onFreeChat={() => {}} onWorkflow={() => {}} />);
    // "New…" (ellipsis) signals the button opens a picker of what to create,
    // rather than a context-free "New".
    expect(screen.getByTestId("new-item-button")).toHaveTextContent("New…");
  });

  it("opens a free chat from the menu", () => {
    const onFreeChat = vi.fn();
    render(
      <NewItemPicker workflows={WORKFLOWS} onFreeChat={onFreeChat} onWorkflow={() => {}} />,
    );
    fireEvent.click(screen.getByTestId("new-item-button"));
    fireEvent.click(screen.getByTestId("new-item-free"));
    expect(onFreeChat).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("new-item-menu")).not.toBeInTheDocument();
  });

  it("launches a workflow from the same menu", () => {
    const onWorkflow = vi.fn();
    render(
      <NewItemPicker workflows={WORKFLOWS} onFreeChat={() => {}} onWorkflow={onWorkflow} />,
    );
    fireEvent.click(screen.getByTestId("new-item-button"));
    expect(screen.getByTestId("new-item-workflow-memory")).toHaveTextContent("Digest into memory");
    fireEvent.click(screen.getByTestId("new-item-workflow-collections"));
    expect(onWorkflow).toHaveBeenCalledWith("collections");
  });

  it("still offers Free chat when the profile has no workflows", () => {
    render(<NewItemPicker workflows={[]} onFreeChat={() => {}} onWorkflow={() => {}} />);
    fireEvent.click(screen.getByTestId("new-item-button"));
    expect(screen.getByTestId("new-item-free")).toBeInTheDocument();
    expect(screen.queryByTestId("new-item-workflow-memory")).not.toBeInTheDocument();
  });

  it("is inert when disabled", () => {
    const onFreeChat = vi.fn();
    render(
      <NewItemPicker workflows={WORKFLOWS} onFreeChat={onFreeChat} onWorkflow={() => {}} disabled />,
    );
    const btn = screen.getByTestId("new-item-button");
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(screen.queryByTestId("new-item-menu")).not.toBeInTheDocument();
  });
});

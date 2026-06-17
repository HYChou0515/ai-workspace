// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowManifestDTO } from "../api/workflows";
import { NewChatPicker } from "./NewChatPicker";

afterEach(cleanup);

const WORKFLOWS: WorkflowManifestDTO[] = [
  { id: "memory", title: "Digest uploads into memory", phases: [], input_json: "x" },
  { id: "collections", title: "File uploads into collections", phases: [], input_json: "x" },
];

describe("NewChatPicker", () => {
  it("offers [Free chat] + each workflow once opened", () => {
    render(<NewChatPicker workflows={WORKFLOWS} onFreeChat={() => {}} onWorkflow={() => {}} />);
    fireEvent.click(screen.getByTestId("new-chat-button"));
    expect(screen.getByRole("menuitem", { name: "Free chat" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Digest uploads into memory" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "File uploads into collections" })).toBeInTheDocument();
  });

  it("fires onFreeChat and closes the menu", () => {
    const onFreeChat = vi.fn();
    render(<NewChatPicker workflows={WORKFLOWS} onFreeChat={onFreeChat} onWorkflow={() => {}} />);
    fireEvent.click(screen.getByTestId("new-chat-button"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Free chat" }));
    expect(onFreeChat).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("new-chat-menu")).not.toBeInTheDocument();
  });

  it("fires onWorkflow with the chosen workflow id", () => {
    const onWorkflow = vi.fn();
    render(<NewChatPicker workflows={WORKFLOWS} onFreeChat={() => {}} onWorkflow={onWorkflow} />);
    fireEvent.click(screen.getByTestId("new-chat-button"));
    fireEvent.click(screen.getByRole("menuitem", { name: "File uploads into collections" }));
    expect(onWorkflow).toHaveBeenCalledWith("collections");
  });
});

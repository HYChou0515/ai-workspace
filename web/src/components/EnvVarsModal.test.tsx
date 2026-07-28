/**
 * The per-item environment variables panel.
 *
 * The values reach the tools the item's agent runs. They are shown in PLAIN
 * TEXT rather than masked: anyone who can talk to the agent on this item can
 * have it read the delivery file anyway, so masking here would buy nothing real
 * and cost the ability to see a typo in a key.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EnvVarsModal } from "./EnvVarsModal";

afterEach(cleanup);

const open = (envVars: Record<string, string>, onSave = vi.fn(), onClose = vi.fn()) => {
  render(<EnvVarsModal envVars={envVars} onSave={onSave} onClose={onClose} />);
  return { onSave, onClose };
};

const nameBoxes = () => screen.getAllByLabelText(/名稱|Name/);
const valueBoxes = () => screen.getAllByLabelText(/值|Value/);
const save = () => fireEvent.click(screen.getByTestId("env-save"));

describe("EnvVarsModal", () => {
  it("lists one row per variable, with the value readable", () => {
    open({ API_KEY: "sk-1", REGION: "tw" });

    expect(nameBoxes().map((i) => (i as HTMLInputElement).value)).toEqual(["API_KEY", "REGION"]);
    expect(valueBoxes().map((i) => (i as HTMLInputElement).value)).toEqual(["sk-1", "tw"]);
  });

  it("does not mask the value", () => {
    // A masked field cannot be proofread, and the agent can read the delivered
    // file regardless — so masking would cost the only thing it could buy.
    open({ API_KEY: "sk-1" });
    expect(valueBoxes()[0]).toHaveAttribute("type", "text");
  });

  it("says so plainly when there is nothing set yet", () => {
    open({});
    expect(screen.getByTestId("env-empty")).toBeInTheDocument();
    expect(screen.queryAllByLabelText(/名稱|Name/)).toHaveLength(0);
  });

  it("adds a variable", () => {
    const { onSave } = open({});

    fireEvent.click(screen.getByTestId("env-add"));
    fireEvent.change(nameBoxes()[0], { target: { value: "API_KEY" } });
    fireEvent.change(valueBoxes()[0], { target: { value: "sk-1" } });
    save();

    expect(onSave).toHaveBeenCalledWith({ API_KEY: "sk-1" });
  });

  it("deletes a variable", () => {
    const { onSave } = open({ API_KEY: "sk-1", REGION: "tw" });

    fireEvent.click(screen.getAllByTestId("env-delete")[0]);
    save();

    expect(onSave).toHaveBeenCalledWith({ REGION: "tw" });
  });

  it("edits a value in place", () => {
    const { onSave } = open({ API_KEY: "sk-1" });

    fireEvent.change(valueBoxes()[0], { target: { value: "sk-2" } });
    save();

    expect(onSave).toHaveBeenCalledWith({ API_KEY: "sk-2" });
  });

  it("keeps a value exactly as typed", () => {
    // Real keys carry `=`, `#`, quotes and `$`. Nothing between this box and the
    // tool may rewrite them, so the panel must not "helpfully" trim or escape.
    const { onSave } = open({});
    const tricky = "a=b c#d$e`f'g\"h";

    fireEvent.click(screen.getByTestId("env-add"));
    fireEvent.change(nameBoxes()[0], { target: { value: "TOKEN" } });
    fireEvent.change(valueBoxes()[0], { target: { value: tricky } });
    save();

    expect(onSave).toHaveBeenCalledWith({ TOKEN: tricky });
  });

  it("drops a row left without a name", () => {
    // Pressing Add and then changing your mind is the common case; a blank row
    // must not become a nameless variable nobody can find again.
    const { onSave } = open({ API_KEY: "sk-1" });

    fireEvent.click(screen.getByTestId("env-add"));
    save();

    expect(onSave).toHaveBeenCalledWith({ API_KEY: "sk-1" });
  });

  it("closes without saving when cancelled", () => {
    const { onSave, onClose } = open({ API_KEY: "sk-1" });

    fireEvent.change(valueBoxes()[0], { target: { value: "changed" } });
    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("refuses to save two rows with the same name", () => {
    // A map cannot hold both, so one would vanish on save with no explanation.
    const { onSave } = open({ API_KEY: "sk-1" });

    fireEvent.click(screen.getByTestId("env-add"));
    fireEvent.change(nameBoxes()[1], { target: { value: "API_KEY" } });
    save();

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId("env-error")).toHaveTextContent("API_KEY");
  });
});

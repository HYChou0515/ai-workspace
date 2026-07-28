/**
 * The per-item environment variables panel.
 *
 * ONE text box holding the whole set as `.env` text, not a row per variable.
 * The thing people actually do with these is paste a block in from somewhere
 * else — a colleague, a password manager, another project's `.env` — and a row
 * editor turns that into one Add and two clicks per line.
 *
 * Storage is unchanged (`dict[str, str]`); the text is just how it is edited.
 *
 * Values are shown in plain text rather than masked: anyone who can talk to the
 * agent on this item can have it read the delivery file anyway, so masking here
 * would buy nothing real and cost the ability to see a typo in a key.
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

const box = () => screen.getByTestId("env-text") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const save = () => fireEvent.click(screen.getByTestId("env-save"));

describe("EnvVarsModal", () => {
  it("shows the whole set as one block of .env text", () => {
    open({ API_KEY: "sk-1", REGION: "tw" });
    expect(box().value).toBe("API_KEY=sk-1\nREGION=tw\n");
  });

  it("does not mask anything", () => {
    // A masked field cannot be proofread, and the agent can read the delivered
    // file regardless — so masking would cost the only thing it could buy.
    open({ API_KEY: "sk-1" });
    expect(box().tagName).toBe("TEXTAREA");
  });

  it("starts empty when nothing is set yet", () => {
    open({});
    expect(box().value).toBe("");
  });

  it("saves what was pasted in, as a map", () => {
    // The point of the text box: a block pasted from somewhere else lands in
    // one gesture instead of one Add and two clicks per line.
    const { onSave } = open({});

    type("FOO=BAR\nBAZ=HOO\n");
    save();

    expect(onSave).toHaveBeenCalledWith({ FOO: "BAR", BAZ: "HOO" });
  });

  it("edits and deletes are both just editing the text", () => {
    const { onSave } = open({ API_KEY: "sk-1", REGION: "tw" });

    type("API_KEY=sk-2\n"); // REGION deleted by not being there any more
    save();

    expect(onSave).toHaveBeenCalledWith({ API_KEY: "sk-2" });
  });

  it("keeps a value exactly as typed", () => {
    // Real keys carry `=`, `#`, quotes and `$`. Nothing between this box and
    // the tool may rewrite them.
    const { onSave } = open({});
    const tricky = "a=b c$d`e'f\"g";

    type(`TOKEN=${tricky}\n`);
    save();

    expect(onSave).toHaveBeenCalledWith({ TOKEN: tricky });
  });

  it("ignores blank lines and comments in what was pasted", () => {
    // A `.env` copied from anywhere real carries both.
    const { onSave } = open({});

    type("# from the ops runbook\n\nAPI_KEY=sk-1\n\n");
    save();

    expect(onSave).toHaveBeenCalledWith({ API_KEY: "sk-1" });
  });

  it("closes without saving when cancelled", () => {
    const { onSave, onClose } = open({ API_KEY: "sk-1" });

    type("API_KEY=changed\n");
    fireEvent.click(screen.getByTestId("env-cancel"));

    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});

describe("EnvVarsModal import / export", () => {
  it("merges an imported file into what is in the box", async () => {
    // Import MERGES: a name the file mentions is overwritten, one it does not
    // is left alone. Replace-all would silently delete variables the file
    // happens not to carry.
    const { onSave } = open({ API_KEY: "old", REGION: "tw" });

    const input = screen.getByTestId("env-import") as HTMLInputElement;
    const text = "API_KEY=new\nEXTRA=1\n";
    const file = new File([text], ".env", { type: "text/plain" });
    Object.defineProperty(file, "text", { value: () => Promise.resolve(text) });
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByDisplayValue(/API_KEY=new/);

    save();

    expect(onSave).toHaveBeenCalledWith({ API_KEY: "new", REGION: "tw", EXTRA: "1" });
  });

  it("exports what is in the box, including unsaved edits", async () => {
    // What you are looking at is what you get — exporting the last SAVED state
    // would hand back a file that silently disagrees with the panel.
    const created: string[] = [];
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: (b: Blob) => {
        void b.text().then((t) => created.push(t));
        return "blob:x";
      },
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: () => {} });

    open({ API_KEY: "sk-1" });
    type("API_KEY=sk-2\n");
    fireEvent.click(screen.getByTestId("env-export"));
    await new Promise((r) => setTimeout(r, 0));

    expect(created).toEqual(["API_KEY=sk-2\n"]);

    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: origCreate });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: origRevoke });
  });
});

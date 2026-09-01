/**
 * The per-item environment variables panel.
 *
 * A text box for the whole set as `.env` text — the thing people actually do
 * with these is paste a block in from somewhere else — plus, since #750, a
 * field for each variable the item's tools said they want.
 *
 * Storage is unchanged (`dict[str, str]`) and there is only ever one copy of a
 * value: the fields edit the box's text, the box is what Save parses.
 *
 * Values are shown in plain text rather than masked: anyone who can talk to the
 * agent on this item can have it read the delivery file anyway, so masking here
 * would buy nothing real and cost the ability to see a typo in a key.
 */
// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemToolState } from "../api/types";
import { renderWithQuery } from "../test/queryWrapper";
import { EnvVarsModal } from "./EnvVarsModal";

afterEach(cleanup);

// Wrapped even without slug/itemId: the panel now reads the item's declared
// tools through the same query the tool picker uses, and a hook needs its
// provider whether or not it is enabled. Given no item, it asks for nothing —
// which is the case these tests cover, the box on its own.
const open = (envVars: Record<string, string>, onSave = vi.fn(), onClose = vi.fn()) => {
  renderWithQuery(<EnvVarsModal envVars={envVars} onSave={onSave} onClose={onClose} />);
  return { onSave, onClose };
};

const box = () => screen.getByTestId("env-text") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const save = () => fireEvent.click(screen.getByTestId("env-save"));

describe("EnvVarsModal declared fields (#750)", () => {
  const SAP: ItemToolState[] = [
    {
      key: "sap-tools",
      label: "SAP Tools",
      description: "",
      default_on: true,
      pref: "follow",
      effective: true,
      env_needs: [
        { name: "SAP_HOST", description: "SAP server address", required: true },
        { name: "SAP_PROXY", description: "", required: false },
      ],
    },
    {
      key: "legacy",
      label: "Legacy Tool",
      description: "",
      default_on: true,
      pref: "follow",
      effective: true,
      env_needs: null,
    },
  ];

  const openWith = (tools: ItemToolState[], envVars: Record<string, string> = {}) => {
    const onSave = vi.fn();
    renderWithQuery(
      <EnvVarsModal
        envVars={envVars}
        onSave={onSave}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => tools),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
      />,
    );
    return { onSave };
  };

  it("offers a field per declared variable, with what the author said it is", async () => {
    openWith(SAP);
    const field = (await screen.findByTestId("env-field-SAP_HOST")) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(screen.getByText("SAP server address")).toBeInTheDocument();
  });

  it("types a declared field straight into the same stored set", async () => {
    const { onSave } = openWith(SAP, { EXISTING: "1" });
    fireEvent.change(await screen.findByTestId("env-field-SAP_HOST"), {
      target: { value: "sap.corp" },
    });
    save();
    // One storage, one dict — the form is a second way to edit the text box,
    // not a second place values live.
    expect(onSave).toHaveBeenCalledWith({ EXISTING: "1", SAP_HOST: "sap.corp" });
  });

  it("shows one tool at a time, and the shared value is the same one", async () => {
    // Someone who just switched a tool on wants that tool's variables, not a
    // scroll past everything else. The tab is a FILTER over one set of values,
    // never a second form: CORP_PROXY under either tab is one stored name with
    // one value, so a tab that kept its own copy would let the same variable
    // hold two different things depending on where you looked.
    const shared = { name: "CORP_PROXY", description: "", required: null };
    const tools: ItemToolState[] = [
      {
        key: "sap-tools",
        label: "SAP Tools",
        description: "",
        default_on: true,
        pref: "follow",
        effective: true,
        env_needs: [{ name: "SAP_HOST", description: "", required: true }, shared],
      },
      {
        key: "wafer",
        label: "Wafer History",
        description: "",
        default_on: true,
        pref: "follow",
        effective: true,
        env_needs: [shared],
      },
    ];
    openWith(tools);

    // First tool's fields are what you land on.
    await screen.findByTestId("env-field-SAP_HOST");
    fireEvent.change(screen.getByTestId("env-field-CORP_PROXY"), {
      target: { value: "proxy:3128" },
    });

    fireEvent.click(screen.getByTestId("env-tab-wafer"));

    // The other tool's tab hides what belongs to the first…
    expect(screen.queryByTestId("env-field-SAP_HOST")).not.toBeInTheDocument();
    // …and the variable they share carries the value typed under the other tab.
    expect((screen.getByTestId("env-field-CORP_PROXY") as HTMLInputElement).value).toBe(
      "proxy:3128",
    );
    // And it says who else is relying on it, so clearing it is an informed act.
    expect(screen.getByTestId("env-shared-CORP_PROXY")).toHaveTextContent("SAP Tools");
  });

  it("typing in a field does not eat what is written in the box", async () => {
    // Asserted HERE, not only over `setEnvValue`, because this is where a
    // person meets it: they annotate the box, reach for a field, and the notes
    // must still be there. A unit test on the helper cannot see the modal
    // choosing to rebuild the text some other way.
    openWith(SAP);
    await screen.findByTestId("env-field-SAP_HOST");
    type("# ask ops first\nEXISTING=1\n\nHALF_TYPED");

    fireEvent.change(screen.getByTestId("env-field-SAP_HOST"), { target: { value: "sap.corp" } });

    expect(box().value).toContain("# ask ops first");
    expect(box().value).toContain("HALF_TYPED");
    expect(box().value).toContain("SAP_HOST=sap.corp");
  });

  it("says what is still missing, and stops saying it once it is filled", async () => {
    // The whole reason this feature exists: "I do not know what I am still
    // missing". Deriving the answer and not putting it on screen would leave
    // the panel exactly as unhelpful as before, with more code behind it.
    openWith(SAP);
    const summary = await screen.findByTestId("env-missing");
    expect(summary).toHaveTextContent("SAP_HOST");
    // SAP_PROXY is explicitly optional, so it is not something you are missing.
    expect(summary).not.toHaveTextContent("SAP_PROXY");

    fireEvent.change(screen.getByTestId("env-field-SAP_HOST"), { target: { value: "sap.corp" } });

    // And it must be able to say the good news, or it only ever nags.
    // Asserted by what it stops naming rather than by the wording: the copy is
    // localized, and CI runs this under a different locale than a laptop does,
    // so matching a phrase would pass here and fail there for no real reason.
    await waitFor(() =>
      expect(screen.getByTestId("env-missing")).not.toHaveTextContent("SAP_HOST"),
    );
  });

  it("says a tool did not declare rather than showing it as satisfied", async () => {
    openWith(SAP);
    expect(await screen.findByTestId("env-undeclared")).toHaveTextContent("Legacy Tool");
  });

  it("offers a login for the variables it can fill, and fills them without saving", async () => {
    // The join is the variable NAME: SAP Tools asked for SAP_HOST, this deploy
    // can produce SAP_HOST, so the button appears. The tool never named the
    // provider — it could not, and that is the point.
    const onSave = vi.fn();
    const resolveEnvProvider = vi.fn(async () => ({ SAP_HOST: "sap.corp", SAP_TOKEN: "tok" }));
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={onSave}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => SAP),
          getEnvProviders: vi.fn(async () => [
            {
              id: "sap-login",
              label: "SAP production login",
              produces: ["SAP_HOST", "SAP_TOKEN"],
              inputs: [
                { name: "user", label: "Account", secret: false },
                { name: "password", label: "Password", secret: true },
              ],
            },
          ]),
          resolveEnvProvider,
        }}
      />,
    );

    fireEvent.click(await screen.findByTestId("env-provider-sap-login"));

    // The dialog is described by the provider — only it knows what its own
    // system needs collected.
    fireEvent.change(screen.getByTestId("env-cred-user"), { target: { value: "alice" } });
    const secret = screen.getByTestId("env-cred-password") as HTMLInputElement;
    expect(secret.type).toBe("password");
    fireEvent.change(secret, { target: { value: "hunter2" } });
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    // Everything it returned lands in the form, including SAP_TOKEN, which the
    // declaration never mentioned — filtering to declared names would drop
    // exactly what an incomplete declaration most needs to keep.
    await waitFor(() =>
      expect((screen.getByTestId("env-field-SAP_HOST") as HTMLInputElement).value).toBe("sap.corp"),
    );
    expect(box().value).toContain("SAP_TOKEN=tok");

    // And NOTHING was stored. The panel is a draft surface: Import merges into
    // the box too, and one dialog must not have two save semantics.
    expect(onSave).not.toHaveBeenCalled();
    expect(resolveEnvProvider).toHaveBeenCalledWith("rca", "i1", "sap-login", {
      user: "alice",
      password: "hunter2",
    });
  });

  it("refuses a value the box cannot hold instead of storing half of it", async () => {
    // `.env` text is one line per variable, so a value with a newline — a PEM
    // certificate, which the seam names as a thing a provider might mint —
    // reads back as its first line and nothing else. Silently. The person then
    // saves a credential that is 30 characters of header and fails somewhere
    // with no connection to here.
    //
    // So it is refused, whole, and named. This panel cannot express such a
    // value; being told that is recoverable, and being handed a truncated
    // certificate is not.
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={vi.fn()}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => SAP),
          getEnvProviders: vi.fn(async () => [
            {
              id: "sap-login",
              label: "SAP production login",
              produces: ["SAP_HOST"],
              inputs: [{ name: "password", label: "Password", secret: true }],
            },
          ]),
          resolveEnvProvider: vi.fn(async () => ({
            SAP_HOST: "sap.corp",
            CLIENT_CERT: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
          })),
        }}
      />,
    );

    fireEvent.click(await screen.findByTestId("env-provider-sap-login"));
    fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    // Named, so the person knows which one and can go and ask for it another way.
    expect(await screen.findByTestId("env-cred-error")).toHaveTextContent("CLIENT_CERT");
    // Nothing merged — not even the variable that WOULD have fitted. Half an
    // exchange applied is a state nobody asked for and nobody can see.
    expect(box().value).not.toContain("BEGIN CERTIFICATE");
    expect(box().value).not.toContain("sap.corp");
  });

  it("keeps the panel untouched when the exchange fails, and says so", async () => {
    renderWithQuery(
      <EnvVarsModal
        envVars={{ KEEP: "me" }}
        onSave={vi.fn()}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => SAP),
          getEnvProviders: vi.fn(async () => [
            {
              id: "sap-login",
              label: "SAP production login",
              produces: ["SAP_HOST"],
              inputs: [{ name: "password", label: "Password", secret: true }],
            },
          ]),
          // Shaped like the real client's failure — an HttpError carrying the
          // envelope in  and the implementation's sentence in
          // . A double whose message was already the sentence is
          // what let the raw envelope reach a real screen unnoticed.
          resolveEnvProvider: vi.fn(async () => {
            const err = new Error(
              '400 Bad Request: {"detail":{"error":"env_provider_failed","why":"wrong password"}}',
            ) as Error & { detail?: unknown };
            err.detail = { error: "env_provider_failed", why: "wrong password" };
            throw err;
          }),
        }}
      />,
    );

    fireEvent.click(await screen.findByTestId("env-provider-sap-login"));
    fireEvent.change(screen.getByTestId("env-cred-password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByTestId("env-cred-submit"));

    // Told, not silently ignored — a dialog that closes on a bad password looks
    // exactly like one that worked.
    expect(await screen.findByTestId("env-cred-error")).toBeInTheDocument();
    // And told in words. Driving this in a real browser showed the panel
    // printing `400 Bad Request: {"detail":{"error":"env_provider_failed",...}}`
    // at someone who had only mistyped a password: every test until now handed
    // it an Error whose message was already a sentence, so nothing saw it.
    const shown = screen.getByTestId("env-cred-error").textContent ?? "";
    expect(shown).not.toMatch(/\d{3} |detail|env_provider_failed|\{/);
    // And what was already typed survives: a failed exchange must not cost
    // someone the rest of their edits.
    expect(box().value).toContain("KEEP=me");
  });

  it("offers no login when this deploy has none", async () => {
    renderWithQuery(
      <EnvVarsModal
        envVars={{}}
        onSave={vi.fn()}
        onClose={vi.fn()}
        slug="rca"
        itemId="i1"
        client={{
          getItemTools: vi.fn(async () => SAP),
          getEnvProviders: vi.fn(async () => []),
          resolveEnvProvider: vi.fn(),
        }}
      />,
    );
    await screen.findByTestId("env-field-SAP_HOST");
    // No implementations is the absence of the feature, not a broken one: the
    // field is still there and typing into it still works.
    expect(screen.queryByTestId("env-provider-sap-login")).not.toBeInTheDocument();
  });

  it("leaves Save usable with a required field empty", async () => {
    // The declaration is a hint, not a gate: nothing about it may stop someone
    // storing what they have. A disabled Save (or a red field) would be a gate
    // wearing a different coat — the button works, but the screen says it
    // should not be pressed, so nobody presses it.
    openWith(SAP);
    await screen.findByTestId("env-field-SAP_HOST");
    expect(screen.getByTestId("env-save")).not.toBeDisabled();
    expect(screen.getByTestId("env-field-SAP_HOST")).toHaveAttribute("aria-invalid", "false");
  });
});

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

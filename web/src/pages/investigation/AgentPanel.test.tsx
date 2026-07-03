// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import { kbApi } from "../../api/kb";
import { DialogProvider } from "../../components/Dialog";
import type { AgentState } from "../../hooks/useAgent";
import { renderWithQuery } from "../../test/queryWrapper";
import { AgentPanel, CHAT_COLUMN_MAX_W } from "./AgentPanel";

function stubAgent(): AgentState {
  return {
    investigationId: "it1",
    log: { entries: [], streaming: false } as unknown as AgentState["log"],
    send: vi.fn(async () => {}),
    mention: vi.fn(async () => {}),
    cancel: vi.fn(),
    undo: vi.fn(async () => {}),
  };
}

function renderPanel(uploadDir = "uploads") {
  return renderWithQuery(
    <DialogProvider>
      <AgentPanel
        investigationId="it1"
        agent={stubAgent()}
        picker={[]}
        suggestions={[]}
        attachedPreset=""
        onAttachPreset={() => {}}
        uploadDir={uploadDir}
      />
    </DialogProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(kbApi, "listCollections").mockResolvedValue([]);
  // #245: the composer's UsageBar queries this; stub so it doesn't hit the network.
  vi.spyOn(api, "getWorkspaceUsage").mockResolvedValue({ used: 0, quota: 0 });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AgentPanel content column width", () => {
  // At wide / full-width chat the message text must not run edge-to-edge — the
  // feed and composer content sit in a centred, capped reading column so there's
  // left/right whitespace. At a narrow panel the cap never engages (RCA untouched).
  it("caps and centres the message feed content", () => {
    renderPanel();
    const col = screen.getByTestId("chat-column");
    expect(col.style.maxWidth).toBe(`${CHAT_COLUMN_MAX_W}px`);
    expect(col.style.marginLeft).toBe("auto");
    expect(col.style.marginRight).toBe("auto");
  });

  it("caps and centres the composer content to the same column", () => {
    renderPanel();
    const col = screen.getByTestId("composer-column");
    expect(col.style.maxWidth).toBe(`${CHAT_COLUMN_MAX_W}px`);
    expect(col.style.marginLeft).toBe("auto");
    expect(col.style.marginRight).toBe("auto");
  });
});

describe("AgentPanel attach (#198)", () => {
  it("stages a dropped file into the profile's upload_dir and injects its path", async () => {
    const upload = vi.spyOn(api, "uploadFile").mockResolvedValue();
    const { container } = renderPanel("dropbox");

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "report.csv")] } });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith(
        "", // useWorkspaceSlug default in tests (no provider)
        "it1",
        "/dropbox/report.csv",
        expect.any(File),
        expect.anything(),
      ),
    );
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toContain("/dropbox/report.csv"));
  });

  it("attaches a non-text, larger-than-256KB file (the old gate is gone)", async () => {
    const upload = vi.spyOn(api, "uploadFile").mockResolvedValue();
    const { container } = renderPanel();
    const big = new File([new Uint8Array(512 * 1024)], "scan.bin");

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [big] } });

    await waitFor(() =>
      expect(upload).toHaveBeenCalledWith(
        "",
        "it1",
        "/uploads/scan.bin",
        expect.any(File),
        expect.anything(),
      ),
    );
  });

  it("alerts but keeps the survivors when one file is too large (413)", async () => {
    const alertSpy = vi.fn();
    vi.stubGlobal("alert", alertSpy);
    vi.spyOn(api, "uploadFile").mockImplementation(async (_s, _i, path) => {
      if (path === "/uploads/big.bin") throw Object.assign(new Error("413"), { status: 413 });
    });
    const { container } = renderPanel();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "big.bin"), new File(["y"], "ok.csv")] },
    });

    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toContain("/uploads/ok.csv"));
    expect(composer.value).not.toContain("big.bin");
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    expect(alertSpy.mock.calls[0][0]).toContain("size limit");
  });
});

describe("AgentPanel image chip (#364)", () => {
  function renderWithAgent(uploadDir = "uploads") {
    const agent = stubAgent();
    renderWithQuery(
      <DialogProvider>
        <AgentPanel
          investigationId="it1"
          agent={agent}
          picker={[]}
          suggestions={[]}
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir={uploadDir}
        />
      </DialogProvider>,
    );
    return agent;
  }
  const imageFile = () => new File([new Uint8Array(4)], "shot.png", { type: "image/png" });
  function selectImage(file = imageFile()) {
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
  }
  const sentText = (agent: AgentState) =>
    (agent.send as ReturnType<typeof vi.fn>).mock.calls[0]![0] as string;

  beforeEach(() => {
    // happy-dom doesn't back object URLs — stub so thumbnails don't throw.
    Object.assign(URL, { createObjectURL: () => "blob:mock", revokeObjectURL: () => {} });
  });

  it("shows a preview chip for an attached image and keeps the composer clean", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue();
    renderWithAgent();
    selectImage();
    expect(await screen.findByTestId("image-chip")).toBeInTheDocument();
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    expect(composer.value).not.toContain("shot.png");
  });

  it("sends the image path in the message and clears the chip", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue();
    const agent = renderWithAgent();
    selectImage();
    await screen.findByTestId("image-chip");
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "what is this" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(sentText(agent)).toContain("/uploads/shot.png");
    expect(sentText(agent)).toContain("what is this");
    expect(screen.queryByTestId("image-chip")).not.toBeInTheDocument();
  });

  it("can send with only an image and no typed text", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue();
    const agent = renderWithAgent();
    selectImage();
    await screen.findByTestId("image-chip");
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(sentText(agent)).toContain("/uploads/shot.png");
  });

  it("removing the chip drops the image so an empty message won't send", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue();
    const agent = renderWithAgent();
    selectImage();
    const chip = await screen.findByTestId("image-chip");
    fireEvent.click(within(chip).getByRole("button"));
    expect(screen.queryByTestId("image-chip")).not.toBeInTheDocument();
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(agent.send).not.toHaveBeenCalled();
  });

  const fileClip = (file: File) => ({
    items: [{ kind: "file", type: file.type, getAsFile: () => file }],
    files: [file],
  });

  it("pasting an image adds a preview chip", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue();
    renderWithAgent();
    const composer = screen.getByPlaceholderText("Ask the agent…");
    fireEvent.paste(composer, {
      clipboardData: fileClip(new File([new Uint8Array(3)], "image.png", { type: "image/png" })),
    });
    expect(await screen.findByTestId("image-chip")).toBeInTheDocument();
  });

  it("pasting a non-image file injects its path (not a chip)", async () => {
    const upload = vi.spyOn(api, "uploadFile").mockResolvedValue();
    renderWithAgent();
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.paste(composer, {
      clipboardData: fileClip(new File(["x"], "data.csv", { type: "text/csv" })),
    });
    await waitFor(() => expect(composer.value).toContain("/uploads/data.csv"));
    expect(screen.queryByTestId("image-chip")).not.toBeInTheDocument();
    expect(upload).toHaveBeenCalled();
  });

  it("pasting plain text is not hijacked", async () => {
    const upload = vi.spyOn(api, "uploadFile").mockResolvedValue();
    renderWithAgent();
    const composer = screen.getByPlaceholderText("Ask the agent…");
    fireEvent.paste(composer, {
      clipboardData: { items: [{ kind: "string", type: "text/plain", getAsFile: () => null }], files: [] },
    });
    expect(screen.queryByTestId("image-chip")).not.toBeInTheDocument();
    expect(upload).not.toHaveBeenCalled();
  });

  it("dropping a file stages it into the composer", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue();
    renderWithAgent();
    const form = document.querySelector("form") as HTMLFormElement;
    fireEvent.drop(form, { dataTransfer: { items: [], files: [new File(["x"], "d.csv")] } });
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toContain("/uploads/d.csv"));
  });
});

describe("AgentPanel applied skills (#380)", () => {
  const skill = {
    name: "designed-pptx",
    description: "polished slides",
    source: "shared",
    default_on: false,
    pref: "follow",
    effective: true,
  };

  function renderWithAgent() {
    const agent = stubAgent();
    renderWithQuery(
      <DialogProvider>
        <AgentPanel
          investigationId="it1"
          agent={agent}
          picker={[]}
          suggestions={[]}
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir="uploads"
        />
      </DialogProvider>,
    );
    return agent;
  }

  // Open the Skills panel, queue the sample skill "for this turn", then dismiss the
  // panel so the composer underneath is reachable again.
  async function queueSkill() {
    vi.spyOn(api, "getItemSkills").mockResolvedValue([skill] as never);
    fireEvent.click(screen.getByTestId("skills-button"));
    fireEvent.click(await screen.findByTestId("skill-apply-designed-pptx"));
    fireEvent.click(screen.getByTestId("skills-modal").parentElement!);
  }

  it("queuing a skill shows an applied-skill chip in the composer", async () => {
    renderWithAgent();
    await queueSkill();
    expect(await screen.findByTestId("applied-skill-chip")).toHaveTextContent("designed-pptx");
  });

  it("sends the queued skills with the turn and clears the chip", async () => {
    const agent = renderWithAgent();
    await queueSkill();
    await screen.findByTestId("applied-skill-chip");
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "make slides" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(agent.send).toHaveBeenCalledWith("make slides", { applySkills: ["designed-pptx"] });
    expect(screen.queryByTestId("applied-skill-chip")).not.toBeInTheDocument();
  });

  it("removing the applied chip drops it from the next send", async () => {
    const agent = renderWithAgent();
    await queueSkill();
    const chip = await screen.findByTestId("applied-skill-chip");
    fireEvent.click(within(chip).getByRole("button"));
    expect(screen.queryByTestId("applied-skill-chip")).not.toBeInTheDocument();
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "hi" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await waitFor(() => expect(agent.send).toHaveBeenCalled());
    expect(agent.send).toHaveBeenCalledWith("hi", { applySkills: [] });
  });
});

describe("AgentPanel steer (#288)", () => {
  it("a run-chat composer steers the run instead of sending an interactive turn", () => {
    const onSteer = vi.fn();
    const agent = stubAgent();
    renderWithQuery(
      <DialogProvider>
        <AgentPanel
          investigationId="it1"
          agent={agent}
          picker={[]}
          suggestions={[]}
          attachedPreset=""
          onAttachPreset={() => {}}
          onSteer={onSteer}
        />
      </DialogProvider>,
    );
    const composer = screen.getByPlaceholderText(/Tell the run what to change/) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "use the X collection" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(onSteer).toHaveBeenCalledWith("use the X collection");
    expect(agent.send).not.toHaveBeenCalled(); // NOT a normal interactive turn
    expect(composer.value).toBe(""); // draft cleared
  });
});

describe("AgentPanel undo restores draft (#370)", () => {
  const userMsg = (content: string) => ({ kind: "message", message: { role: "user", content } });
  const assistantMsg = (content: string) => ({
    kind: "message",
    message: { role: "assistant", content },
  });

  function renderWithLog(entries: unknown[]) {
    const agent = stubAgent();
    agent.log = { entries, streaming: false } as unknown as AgentState["log"];
    renderWithQuery(
      <DialogProvider>
        <AgentPanel
          investigationId="it1"
          agent={agent}
          picker={[]}
          suggestions={[]}
          attachedPreset=""
          onAttachPreset={() => {}}
          uploadDir="uploads"
        />
      </DialogProvider>,
    );
    return agent;
  }

  const confirmUndo = async () =>
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));

  it("puts the undone user message back into the composer and focuses it", async () => {
    const agent = renderWithLog([userMsg("原本這句話")]);
    fireEvent.click(screen.getByRole("button", { name: /復原/ }));
    await confirmUndo();
    await waitFor(() => expect(agent.undo).toHaveBeenCalledWith(1));
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toBe("原本這句話"));
    expect(composer).toHaveFocus();
  });

  it("restores the clicked (earliest) message when undoing multiple turns", async () => {
    const agent = renderWithLog([
      userMsg("最早的問題"),
      assistantMsg("回覆"),
      userMsg("後來的問題"),
    ]);
    // Undo on the FIRST user prompt removes both turns (2 user messages onward).
    fireEvent.click(screen.getAllByRole("button", { name: /復原/ })[0]!);
    await confirmUndo();
    await waitFor(() => expect(agent.undo).toHaveBeenCalledWith(2));
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toBe("最早的問題"));
  });

  it("overwrites an in-progress draft with the restored message", async () => {
    const agent = renderWithLog([userMsg("被復原的訊息")]);
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "half-typed next message" } });
    fireEvent.click(screen.getByRole("button", { name: /復原/ }));
    await confirmUndo();
    await waitFor(() => expect(agent.undo).toHaveBeenCalledWith(1));
    await waitFor(() => expect(composer.value).toBe("被復原的訊息"));
  });

  it("leaves the draft untouched when undo fails", async () => {
    vi.stubGlobal("alert", vi.fn());
    const agent = renderWithLog([userMsg("原句")]);
    (agent.undo as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("boom"));
    const composer = screen.getByPlaceholderText("Ask the agent…") as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "keep me" } });
    fireEvent.click(screen.getByRole("button", { name: /復原/ }));
    await confirmUndo();
    await waitFor(() => expect(agent.undo).toHaveBeenCalled());
    expect(composer.value).toBe("keep me");
  });
});

// @vitest-environment happy-dom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { type Locale, LocaleProvider, translate } from "../lib/i18n";
import { MY_RESOURCES_PATH, RESOURCE_LINK_KEYS, ResourceLinkText } from "./ResourceLinkText";

afterEach(cleanup);

const show = (text: string) =>
  render(
    <MemoryRouter>
      <ResourceLinkText text={text} />
    </MemoryRouter>,
  );

const LOCALES: Locale[] = ["zh-TW", "en"];

describe("<ResourceLinkText /> (#692)", () => {
  it("turns the page's name into a link to it, keeping the sentence intact", () => {
    show(translate("zh-TW", "terminal.envFull"));
    const link = screen.getByRole("link", { name: translate("zh-TW", "resources.title") });
    expect(link).toHaveAttribute("href", "/my-resources");
    // The remedy still reads as one sentence — the link replaced the phrase,
    // it was not appended as a stray "click here".
    expect(screen.getByText(/執行環境已達上限/)).toBeInTheDocument();
    expect(screen.getByText(/關掉不用的環境再試一次/)).toBeInTheDocument();
  });

  it("links the English wording too, so the phrase is not hardcoded", () => {
    localStorage.setItem("ws.locale", "en");
    try {
      render(
        <MemoryRouter>
          <LocaleProvider>
            <ResourceLinkText text={translate("en", "terminal.envFull")} />
          </LocaleProvider>
        </MemoryRouter>,
      );
      expect(screen.getByRole("link", { name: "My resources" })).toHaveAttribute(
        "href",
        "/my-resources",
      );
    } finally {
      localStorage.removeItem("ws.locale");
    }
  });

  // An href is not the claim being made — #692 is that the person can GET
  // there. Press it and land.
  it("actually navigates to the page when pressed", async () => {
    render(
      <MemoryRouter initialEntries={["/a/rca/items/it1"]}>
        <Routes>
          <Route
            path="/a/rca/items/it1"
            element={<ResourceLinkText text={translate("zh-TW", "chat.send.userFull")} />}
          />
          <Route path={MY_RESOURCES_PATH} element={<p>資源頁</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("link"));
    expect(await screen.findByText("資源頁")).toBeInTheDocument();
  });

  it("leaves a message that names no destination exactly as it was", () => {
    show("boom: exec failed");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("boom: exec failed")).toBeInTheDocument();
  });

  // A composed line (the composer joins several files' outcomes) still carries
  // the phrase, so it still gets the link.
  it("links the phrase inside a composed line", () => {
    show(`big.bin — ${translate("zh-TW", "workspace.overQuota.user", { names: "big.bin" })}`);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/my-resources");
  });
});

describe("the messages that promise the page (#692 guard)", () => {
  // The link is produced by finding the page's own name inside the sentence.
  // That is only sound while every message that sends someone there spells the
  // name the catalog spells it — reword one to 「資源頁」 and the sentence would
  // still read fine while quietly becoming unclickable again, which is exactly
  // the defect this issue is about. Fail here instead.
  it.each(RESOURCE_LINK_KEYS)("%s names the page verbatim in every locale", (key) => {
    for (const locale of LOCALES) {
      expect(translate(locale, key)).toContain(translate(locale, "resources.title"));
    }
  });
});

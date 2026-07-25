/**
 * web-demo skill — Playwright recording template with a VISIBLE cursor.
 *
 * Copy this, set BASE/OUT, and fill in the TOUR block with your flow. Run via
 * the playwright-skill executor (`node run.js record.js`) or plain playwright.
 * Headless Chromium shows no OS cursor, so we inject a red dot that follows the
 * mouse (via addInitScript, so it survives navigations) and pulses on click.
 *
 * Golden rules:
 *  - glide the cursor with `move()` (for the visuals), then `click(locator)`
 *    (Playwright's real click — a manual mouse.down/up won't fire a <Link>).
 *  - `dismiss()` any first-visit "Got it" modal before the first real click.
 *  - never `waitUntil:'networkidle'` (SSE keeps a connection open forever).
 */
const { chromium } = require("playwright");
const fs = require("fs");

const BASE = "http://localhost:8011"; // the serve-dist.js origin
const OUT = "/tmp/web-demo"; // where the .webm lands
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    recordVideo: { dir: OUT, size: { width: 1280, height: 800 } },
  });

  // Inject the visible cursor on every document.
  await context.addInitScript(() => {
    const mk = () => {
      if (document.getElementById("__cur")) return;
      const c = document.createElement("div");
      c.id = "__cur";
      Object.assign(c.style, {
        position: "fixed",
        left: "0",
        top: "0",
        width: "22px",
        height: "22px",
        marginLeft: "-11px",
        marginTop: "-11px",
        borderRadius: "50%",
        background: "rgba(240,80,46,0.35)",
        border: "2px solid #F0502E",
        boxShadow: "0 0 8px rgba(0,0,0,0.35)",
        zIndex: "2147483647",
        pointerEvents: "none",
        transition: "transform .08s",
      });
      (document.body || document.documentElement).appendChild(c);
    };
    const upd = (e) => {
      const c = document.getElementById("__cur");
      if (c) {
        c.style.left = e.clientX + "px";
        c.style.top = e.clientY + "px";
      }
    };
    if (document.readyState !== "loading") mk();
    else addEventListener("DOMContentLoaded", mk);
    addEventListener("mousemove", upd, true);
    addEventListener("mousedown", () => {
      const c = document.getElementById("__cur");
      if (c) c.style.transform = "scale(0.55)";
    }, true);
    addEventListener("mouseup", () => {
      const c = document.getElementById("__cur");
      if (c) c.style.transform = "scale(1)";
    }, true);
  });

  const page = await context.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e.message).slice(0, 100)));

  // --- helpers ---------------------------------------------------------------
  const move = async (x, y) => {
    await page.mouse.move(x, y, { steps: 28 }); // smooth glide → the cursor follows
    await page.waitForTimeout(280);
  };
  const box = async (loc) => {
    try {
      await loc.first().waitFor({ timeout: 4000 });
    } catch {
      return null;
    }
    return loc.first().boundingBox();
  };
  const click = async (loc, pause = 800) => {
    const b = await box(loc);
    if (!b) return false;
    await move(b.x + b.width / 2, b.y + b.height / 2);
    try {
      await loc.first().click({ timeout: 4000 }); // real click (reliable nav)
    } catch {
      return false;
    }
    await page.waitForTimeout(pause);
    return true;
  };
  const hover = async (loc) => {
    const b = await box(loc);
    if (b) await move(b.x + b.width / 2, b.y + b.height / 2);
  };
  const dismiss = async () => {
    const b = page.getByRole("button", { name: /Got it|Don't show again/i });
    if (await b.count()) {
      try {
        await b.first().click({ timeout: 2000 });
        await page.waitForTimeout(500);
      } catch {
        /* nothing to dismiss */
      }
    }
  };

  // --- TOUR: customise this block --------------------------------------------
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  await dismiss();
  await move(640, 400);

  // e.g. open an App from the gallery, then click around its chrome:
  await click(page.locator('a[href$="/a/playground"]'), 3000);
  await dismiss();
  await click(page.getByRole("button", { name: /platform menu/i }), 900);
  for (const t of ["Knowledge base", "Review", "Diagnostics", "Help"]) {
    await hover(page.getByRole("menuitem", { name: t }));
  }
  await page.waitForTimeout(600);
  // --- end TOUR --------------------------------------------------------------

  const vpath = await page.video().path();
  await page.close();
  await context.close();
  await browser.close();
  console.log("VIDEO", vpath);
  console.log("ERRS", JSON.stringify(errs.slice(0, 5)));
})();

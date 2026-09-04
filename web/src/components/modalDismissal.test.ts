import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * #779 P6 — the rule for leaving a modal lives in ONE place, and this is what
 * keeps it there.
 *
 * The issue was never a handful of modals behaving badly. It was that "does a
 * click beside the panel close this?" had three different answers plus a
 * Mantine default, because nothing stopped the next person from hand-rolling a
 * fourth overlay. Rules that live only in a doc drift; this one fails a test.
 *
 * Two guards, because a hand-rolled overlay can be spelled two ways:
 *  - a `role="presentation"` wrapper that closes on click (the a11y-correct
 *    spelling), and
 *  - a bare `position: fixed; inset: 0` div with an onClick (the quick one).
 *
 * The allowlist is deliberately short and explicit. A dropdown's click-away
 * catcher looks identical in the source but is not a modal backdrop: it dismisses
 * a menu, there is nothing to lose behind it, and closing on an outside click is
 * the whole point. Adding to this list should feel like a decision.
 */

const SRC = join(new URL(".", import.meta.url).pathname, "..");

/** Owns the shared modal + confirm behaviour — the rule lives here. */
const SHELLS = ["components/ModalShell.tsx", "components/Dialog.tsx"];

/** Click-away catchers for dropdowns/menus, not modal backdrops. A menu holds
 * nothing the user typed, so dismissing it on an outside click is correct. */
const CLICK_AWAY = [
  "components/ModelEffortPicker.tsx",
  "pages/investigation/FileTree.tsx",
  "pages/investigation/WorkspaceShell.tsx",
];

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) tsxFiles(full, out);
    else if (name.endsWith(".tsx") && !name.includes(".test.")) out.push(full);
  }
  return out;
}

/**
 * `=>` inside a JSX attribute ends a naive `[^>]*` tag match early, so anything
 * written after the first arrow function — very often the `style` — was never
 * inspected. `<div onClick={() => onClose()} style={{position:"fixed",inset:0}}>`
 * sailed past both guards until this existed. The first mutation probe missed it
 * purely because it happened to put `style` first, which is the lesson: a probe
 * proves the spelling you thought of, not the rule.
 */
const maskArrows = (s: string) => s.replace(/=>/g, "=\u0001");

/**
 * The body of every `onClick={…}`, brace-matched and STRING-AWARE.
 *
 * A naive counter is fooled by braces inside string and template literals: two
 * `}` in a log line drop the depth to zero early, the body ends before the
 * handler's real content, and a bypass after that point is never seen. Probed —
 * one `}` still gets caught, two slip through. Exported at module scope so the
 * matcher is testable on its own rather than only through whatever happens to
 * be in the tree today.
 */
/** Keywords a regex literal may directly follow — after these a `/` opens a
 * pattern, not a division, even though the character before it is a letter. */
const KEYWORDS = new Set([
  "return",
  "typeof",
  "case",
  "await",
  "void",
  "new",
  "delete",
  "in",
  "of",
  "yield",
  "do",
  "else",
]);

/** The identifier immediately before `idx`, ignoring whitespace. */
function wordBefore(text: string, idx: number): string {
  let j = idx - 1;
  while (j >= 0 && /\s/.test(text[j])) j--;
  const end = j + 1;
  while (j >= 0 && /\w/.test(text[j])) j--;
  return text.slice(j + 1, end);
}

export function onClickBodies(text: string, where = "(inline)"): { body: string; line: number }[] {
  const out: { body: string; line: number }[] = [];
  const re = /onClick=\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    let depth = 1;
    let quote: string | null = null;
    let inRegex = false;
    let prev = ""; // last significant character, to tell `/`-as-regex from divide
    let i = m.index + m[0].length;
    for (; i < text.length && depth > 0; i++) {
      const c = text[i];
      if (quote) {
        if (c === "\\") i++; // skip the escaped character
        else if (c === quote) quote = null;
        continue;
      }
      if (inRegex) {
        if (c === "\\") i++;
        else if (c === "/") inRegex = false;
        continue;
      }
      // Comments first. Skipping them is not tidiness: a `//` that is not
      // recognised leaves its SECOND slash looking like the start of a regex
      // literal, which then swallows the rest of the handler — that is exactly
      // what a real file (EntityRecordModal's "Open file" button, whose body
      // carries two comment lines) did the moment regex support was added. And
      // it is what makes an apostrophe in a comment harmless: "don't" no longer
      // opens a phantom string.
      if (c === "/" && text[i + 1] === "/") {
        while (i < text.length && text[i] !== "\n") i++;
        continue;
      }
      if (c === "/" && text[i + 1] === "*") {
        i += 2;
        while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) i++;
        i++; // land on the `/` so the loop's i++ steps past it
        continue;
      }
      if (c === '"' || c === "'" || c === "`") quote = c;
      // A `/` starts a regex literal only where a VALUE may begin. After an
      // identifier, a number, `)` or `]` it is division.
      //
      // The two misreads fail in OPPOSITE directions, which is why the keyword
      // list matters. Divide-read-as-regex swallows to EOF and trips the
      // assertion below — loud. Regex-read-as-divide truncates the body
      // silently, and `return /…/` hits exactly that: the last character is `n`,
      // a word character, so the character test alone calls it division. Hence
      // the preceding WORD, not the preceding character.
      else if (c === "/" && (!/[\w)\]]/.test(prev) || KEYWORDS.has(wordBefore(text, i)))) {
        inRegex = true;
      } else if (c === "{") depth++;
      else if (c === "}") depth--;
      if (!/\s/.test(c)) prev = c;
    }
    // An unbalanced scan is the SILENT failure mode: the body ends early and a
    // close after that point is skipped without a word. Say so instead — the
    // point of this file is that a gap in the rule cannot be invisible.
    if (depth !== 0) {
      throw new Error(
        `onClickBodies: unbalanced braces in ${where} at line ${text.slice(0, m.index).split("\n").length}. ` +
          "The scanner lost track (a construct it does not understand), so a bypass there would be " +
          "skipped silently.",
      );
    }
    out.push({ body: text.slice(m.index, i), line: text.slice(0, m.index).split("\n").length });
  }
  return out;
}

const files = tsxFiles(SRC).map((f) => {
  const text = readFileSync(f, "utf8");
  return {
    path: relative(SRC, f).split("\\").join("/"),
    text,
    scan: maskArrows(text),
  };
});

describe("#779 — modal dismissal has one owner", () => {
  it("finds source to scan (guards against a broken walker reporting all-clear)", () => {
    expect(files.length).toBeGreaterThan(100);
    expect(files.map((f) => f.path)).toContain("components/ModalShell.tsx");
  });

  it("has no hand-rolled backdrop that closes on click", () => {
    const offenders = files
      .filter((f) => !SHELLS.includes(f.path))
      .filter((f) => /role="presentation"[^>]*onClick|onClick[^>]*role="presentation"/s.test(f.scan))
      .map((f) => f.path);

    expect(offenders).toEqual([]);
  });

  it("has no full-screen fixed overlay with an onClick outside the shells", () => {
    // Matched per opening TAG, not per file: `ItemMembersPanel` has a centred
    // `inset: 0` info box and, elsewhere, an onClick — true of the file, false
    // of any single element. A file-wide match would report it forever, and a
    // guard that cries wolf gets an allowlist entry instead of a fix.
    const hasBackdropTag = (text: string) =>
      (text.match(/<[a-zA-Z][^>]*>/g) ?? []).some(
        (tag) =>
          /onClick/.test(tag) && /position:\s*"fixed"/.test(tag) && /inset:\s*0/.test(tag),
      );

    const offenders = files
      .filter((f) => !SHELLS.includes(f.path) && !CLICK_AWAY.includes(f.path))
      .filter((f) => hasBackdropTag(f.scan))
      .map((f) => f.path);

    expect(offenders).toEqual([]);
  });

  // Found by review, not by me: I wired useDirtyClose to ModalShell's onClose in
  // five modals and left their OWN ✕ / Cancel buttons calling onClose directly.
  // Escape asked; the button people actually click threw the work away in
  // silence. Guarding the shell is not guarding the modal — every deliberate
  // exit has to go through the same handler, including the ones the modal draws
  // itself.
  it("finds a close hidden behind braces in a string literal", () => {
    // Probed with a real mutant: one `}` in a string still got caught, two did
    // not — the depth hit zero before the handler's real content. A guard with a
    // spelling it cannot see is worth less than its green tick suggests.
    const src = [
      "<button",
      "  onClick={() => {",
      '    console.log("}}");',
      "    onClose();",
      "  }}",
      ">x</button>",
    ].join("\n");

    expect(onClickBodies(src)[0].body).toContain("onClose()");
  });

  it("is not fooled by an escaped quote inside that string", () => {
    const src = '<button onClick={() => { f("a\\"}}"); onClose(); }}>x</button>';
    expect(onClickBodies(src)[0].body).toContain("onClose()");
  });


  it("is not truncated by a brace inside a regex literal", () => {
    // The positive control the depth assertion was missing. A stray CLOSING
    // brace lands on depth === 0 exactly, so the body is cut short and nothing
    // is thrown — the assertion is loud about the direction that was already
    // loud, and silent about the one that was already silent. A guarded modal
    // written this way would report clean while dropping the user's work:
    //
    // It takes UNPAIRED closing braces to reach zero early — `/[{}]/` balances
    // and is harmless, and one `\}` only undoes the arrow's own block. Two
    // does it, and the scan then stops before the handler's real content
    // WITHOUT tripping the depth assertion, because it lands on zero exactly.
    const src = [
      "<button",
      "  onClick={() => {",
      '    setName(raw.replace(/\\}/g, "").replace(/\\}/g, ""));',
      "    onClose();",
      "  }}",
      ">x</button>",
    ].join("\n");

    expect(onClickBodies(src)[0].body).toContain("onClose()");
  });

  it("is not derailed by comments inside the handler", () => {
    // Not hypothetical: adding regex support broke on a REAL file
    // (EntityRecordModal's "Open file" button) because the second slash of a
    // `//` comment looked like the start of a regex literal and swallowed the
    // rest of the body. The apostrophe case rides along — "don't" inside a
    // comment no longer opens a phantom string.
    const src = [
      "<button",
      "  onClick={() => {",
      "    openFile(path);",
      "    // hand over rather than stack; don't leave this up",
      "    /* block form too } { unbalanced on purpose */",
      "    onClose();",
      "  }}",
      ">x</button>",
    ].join("\n");

    expect(onClickBodies(src)[0].body).toContain("onClose()");
  });

  it("is not truncated by a regex literal that follows a keyword", () => {
    // The third control. `prev` is the last CHARACTER, so after `return` it is
    // `n` — a word character — and the `/` reads as division. The regex is then
    // scanned as code, its unpaired braces count, and the body truncates before
    // a later onClose(). Same silent shape as the two cases above; only the
    // preceding token differs.
    const src = [
      "<button",
      "  onClick={() => {",
      '    if (guard) return /\\}\\}/.test(raw);',
      "    onClose();",
      "  }}",
      ">x</button>",
    ].join("\n");

    expect(onClickBodies(src)[0].body).toContain("onClose()");
  });
  it("has no close button bypassing the guard in a modal that has one", () => {
    // Scans the BODY of each onClick, brace-matched, not one line at a time.
    // Round 1 found five buttons bypassing the guard; the single-line version
    // written to stop that recurring then missed a sixth in a file it already
    // scanned — `onClick={() => { onSelect(id); onClose(); }}` spans lines and
    // calls rather than binds. A guard for "people keep writing X" has to cover
    // how X is actually written, not the one spelling that prompted it.
    //
    // Only onClick bodies: an `onClose()` at the end of a save handler is the
    // success path, where there is nothing left to lose.
    const CLOSES = /onClick=\{(onClose|close)\}|(?<![.\w])(onClose|close)\(\)/;

    const offenders: string[] = [];
    for (const f of files) {
      if (!f.text.includes("useDirtyClose(")) continue;
      const lines = f.text.split("\n");
      for (const { body, line } of onClickBodies(f.text, f.path)) {
        if (!CLOSES.test(body)) continue;
        // An exemption must be written down immediately above, with a reason —
        // legitimate ones exist (a branch rendered only after a successful
        // submit; a handover that opens the record elsewhere) — so the rule
        // needs a door rather than an exception list kept somewhere else.
        //
        // Walk up through the contiguous comment block rather than a fixed
        // number of lines: a reason worth writing is often longer than the
        // window, and a guard that rejects a well-explained exemption for being
        // too well explained teaches people to write shorter reasons.
        let cursor = line - 1;
        let exempt = false;
        while (cursor > 0) {
          const text = lines[cursor - 1]?.trim() ?? "";
          // JSX comments ({/* … */}) count too — inside JSX that is the only
          // form available, and two of the exemptions live there.
          const isComment =
            text.startsWith("//") ||
            text.startsWith("/*") ||
            text.startsWith("*") ||
            text.startsWith("{/*") ||
            text.endsWith("*/}");
          if (!isComment) break;
          if (text.includes("dirty-close-exempt")) {
            exempt = true;
            break;
          }
          cursor--;
        }
        if (!exempt) offenders.push(`${f.path}:${line}`);
      }
    }

    expect(offenders).toEqual([]);
  });

  it("keeps the safe default: ModalShell does not close on backdrop unless asked", () => {
    const shell = files.find((f) => f.path === "components/ModalShell.tsx")!.text;
    expect(shell).toMatch(/closeOnBackdrop\s*=\s*false/);
  });
});

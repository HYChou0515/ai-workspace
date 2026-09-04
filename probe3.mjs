import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SRC = "/home/hychou/project/kb/ai-workspace/.claude/worktrees/plan-modal-dismiss-779/web/src";
function tsx(dir, out = []) {
  for (const n of readdirSync(dir)) {
    if (n === "node_modules") continue;
    const f = join(dir, n);
    if (statSync(f).isDirectory()) tsx(f, out);
    else if (n.endsWith(".tsx") && !n.includes(".test.")) out.push(f);
  }
  return out;
}

// the guard's matcher, verbatim
const bodies = (text) => {
  const out = [];
  const re = /onClick=\{/g;
  let m;
  while ((m = re.exec(text))) {
    let depth = 1;
    let i = m.index + m[0].length;
    for (; i < text.length && depth > 0; i++) {
      if (text[i] === "{") depth++;
      else if (text[i] === "}") depth--;
    }
    out.push({ body: text.slice(m.index, i), line: text.slice(0, m.index).split("\n").length, depth });
  }
  return out;
};

// 1) does any onClick body in a guarded file fail to close (depth != 0)?
// 2) does any body contain an odd brace inside a string/template literal?
const STRING = /(["'`])(?:\\.|(?!\1)[\s\S])*\1/g;
let flagged = 0;
for (const f of tsx(SRC)) {
  const text = readFileSync(f, "utf8");
  const guarded = text.includes("useDirtyClose(");
  for (const { body, line, depth } of bodies(text)) {
    const stripped = body.replace(STRING, '""');
    const rawBal = [...body].reduce((d, c) => d + (c === "{") - (c === "}"), 0);
    const strBal = [...stripped].reduce((d, c) => d + (c === "{") - (c === "}"), 0);
    if (depth !== 0 || rawBal !== strBal) {
      flagged++;
      console.log(`${guarded ? "GUARDED " : "        "}${relative(SRC, f)}:${line} depth=${depth} rawBal=${rawBal} strBal=${strBal}`);
      console.log("   " + body.slice(0, 160).replace(/\n/g, " ⏎ "));
    }
  }
}
console.log(`\nflagged=${flagged}`);

// 3) exits handed to a child as a prop, which the onClick-only guard cannot see
console.log("\n=== prop-passed exits in guarded files ===");
const PROP = /\bon(Close|Cancel|Dismiss)=\{(onClose|close)\}/;
for (const f of tsx(SRC)) {
  const text = readFileSync(f, "utf8");
  if (!text.includes("useDirtyClose(")) continue;
  text.split("\n").forEach((l, i) => {
    if (PROP.test(l)) console.log(`${relative(SRC, f)}:${i + 1}  ${l.trim()}`);
  });
}

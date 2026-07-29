/**
 * `.env`-shaped text, in and out — for the item's environment variables panel.
 *
 * We parse it ourselves. Handing it to anything shell-flavoured would rewrite a
 * value carrying `$`, a backtick or `$(…)`, and the only symptom of a mangled
 * key is a tool failing somewhere else entirely, with nothing pointing back
 * here. The same reasoning governs the sandbox-side launcher, which exports
 * each line as one word rather than sourcing the file.
 */

/** Parse `.env`-shaped text into a variable map.
 *
 * Deliberately forgiving about the *shape* (blank lines, `#` comments, a
 * leading `export `, CRLF — all of which turn up in a file a user actually has)
 * and deliberately literal about the *value*: everything after the first `=` is
 * kept as written — quotes included, since they are part of the value here
 * rather than syntax — except for edge whitespace, which is invisible, almost
 * always accidental, and fails in a place no one can trace back. A line that
 * carries no assignment is skipped rather than turned into something. */
export function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "");
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const body = trimmed.startsWith("export ") ? trimmed.slice("export ".length) : trimmed;
    const eq = body.indexOf("=");
    if (eq <= 0) continue; // no assignment, or nameless (`=orphan`)
    const name = body.slice(0, eq).trim();
    if (!name) continue;
    // Trimmed on both sides: edge whitespace here is invisible and almost
    // always accidental, and a key carrying a stray trailing space fails
    // somewhere no one can connect back to the file. Whitespace INSIDE the
    // value is untouched, as is every other character.
    out[name] = body.slice(eq + 1).trim();
  }
  return out;
}

/** The map as `.env`-shaped text — what Export downloads. No quoting: the value
 * is written verbatim, which is what makes `parseEnvText(toEnvText(x))` an
 * identity for the characters real keys carry. */
export function toEnvText(envVars: Record<string, string>): string {
  return Object.entries(envVars)
    .map(([name, value]) => `${name}=${value}\n`)
    .join("");
}

/** Import semantics: MERGE. A name the imported file mentions is overwritten; a
 * name it does not mention is left alone.
 *
 * Not replace-all. Pulling a `.env` in from another project to top up a couple
 * of keys would then silently delete everything that file happens not to carry,
 * with nothing on screen to say so — and deleting already has its own button. */
export function mergeEnv(
  current: Record<string, string>,
  imported: Record<string, string>,
): Record<string, string> {
  return { ...current, ...imported };
}

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

/** The names in `entries` that this text format cannot carry unchanged.
 *
 * Asked as a ROUND TRIP, not as a list of forbidden characters: write the pair
 * out, read it back, and see whether it is still the same pair. A blocklist
 * only knows what its author remembered — the first version of this guard
 * tested for newlines, which let a name like `A=B` through, and that stores a
 * DIFFERENT variable than the one on screen with nothing to show for it.
 *
 * The cases this actually catches, none of which were enumerated here: a value
 * carrying a newline (a PEM keeps only its first line), a name containing `=`
 * (silently becomes another variable), a name starting with `#` (vanishes), a
 * value with edge whitespace (trimmed). Adding a rule to the parser adds it
 * here for free.
 *
 * Matters because values now arrive from an `IEnvProvider` rather than only
 * from someone typing (#750): a person can see that a certificate does not fit
 * in a one-line field, and an exchange cannot. */
export function unstorable(entries: Record<string, string>): string[] {
  return Object.entries(entries)
    .filter(([name, value]) => parseEnvText(`${name}=${value}\n`)[name] !== value)
    .map(([name]) => name);
}

/** Set one variable in `.env`-shaped text, keeping everything else verbatim.
 *
 * The obvious implementation — `toEnvText({ ...parseEnvText(text), [name]: v })`
 * — is lossy by construction: a trip through the map keeps only what the map
 * can hold, so comments, blank lines and any line still being typed vanish.
 * That is a fair trade when somebody deliberately Imports a file over their
 * work. It is not one on the path a FORM FIELD takes (#750), where it would
 * mean touching one input silently deletes the notes above it, on a keystroke,
 * while they are looking at it.
 *
 * So the text is edited in place. The LAST line assigning `name` is rewritten —
 * last because that is the one `parseEnvText` keeps, and rewriting any other
 * would leave the panel showing a value the stored set disagrees with. A name
 * not present yet is appended. Everything else comes back exactly as it went in.
 *
 * Line recognition mirrors `parseEnvText` deliberately: same comment rule, same
 * `export ` prefix, same "first `=` wins". If the two ever disagree, this writes
 * to a line the parser does not read, and the panel and the stored set drift
 * apart with nothing on screen to show it. */
export function setEnvValue(text: string, name: string, value: string): string {
  const assigns = (raw: string): boolean => {
    const trimmed = raw.replace(/\r$/, "").trim();
    if (!trimmed || trimmed.startsWith("#")) return false;
    const body = trimmed.startsWith("export ") ? trimmed.slice("export ".length) : trimmed;
    const eq = body.indexOf("=");
    return eq > 0 && body.slice(0, eq).trim() === name;
  };
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    if (assigns(lines[i])) {
      lines[i] = `${name}=${value}`;
      return lines.join("\n");
    }
  }
  // Appended, with the existing trailing newline kept where it was so the first
  // variable added does not read differently from the ones already there.
  const body = text === "" || text.endsWith("\n") ? text : `${text}\n`;
  return `${body}${name}=${value}\n`;
}

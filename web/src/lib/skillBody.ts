/** The SKILL.md without its frontmatter. The name and description are already
 * on the page, and rendered as Markdown a `---` fence under a line of text
 * reads as a setext heading — measured: "name: triage-reflow" drawn as an h1
 * over the real one. Display only; the file itself ships whole. */
export function skillBody(md: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(md);
  return m ? md.slice(m[0].length).replace(/^\s+/, "") : md;
}

/** The body, without a first `# <name>` that only repeats the page's own
 * title right above it (plan-skill-hub-ux-redo D17). Any other first heading
 * is the author's and stays. */
export function skillBodyUnderTitle(md: string, name: string): string {
  const body = skillBody(md);
  const m = /^#[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n|$)/.exec(body);
  return m && m[1].trim() === name ? body.slice(m[0].length).replace(/^\s+/, "") : body;
}

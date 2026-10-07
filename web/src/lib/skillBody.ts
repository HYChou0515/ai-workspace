/** The SKILL.md without its frontmatter. The name and description are already
 * on the page, and rendered as Markdown a `---` fence under a line of text
 * reads as a setext heading — measured: "name: triage-reflow" drawn as an h1
 * over the real one. Display only; the file itself ships whole. */
export function skillBody(md: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(md);
  return m ? md.slice(m[0].length).replace(/^\s+/, "") : md;
}

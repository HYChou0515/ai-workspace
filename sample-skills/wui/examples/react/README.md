# The built example — React + TypeScript

**Copy this folder by default.** Anything past one screen of static markup wants
it. Then, in the sandbox:

```sh
pnpm install --frozen-lockfile   # or `pnpm install` the first time, to write the lock
pnpm build                       # → tsc --noEmit && vite build → dist/
```

`dist/` is the page. `src/` is what you edit. Both live in the workspace.

## Why TypeScript here in particular

A WUI cannot report its own bugs. Its reader does not open a console; when the
page is wrong they can only say "it's broken". So the compiler is the only thing
between a mistake and the person who asked for the page.

`src/wui.d.ts` types the whole `workspace` bridge — copy it unchanged, it
describes the platform rather than your page — and `tsc --noEmit` runs BEFORE
Vite, because Vite strips types without checking them and would happily build
code the compiler rejects. Two shapes are worth the whole exercise on their own:

- `readFile` returns a **union**, so `JSON.parse(file.text)` against a binary
  file is a compile error instead of the word `undefined` on screen.
- `callTool` returns `{ output: string, exit_code: number }` — a **string**,
  with no promise it is JSON. The type says so; run the tool and look before you
  parse it (SKILL.md, "Run the tool before you parse it").

A type error fails the build, which means `dist/` stays as it was and the build
log on screen says why. The page does not silently become wrong.

## Who rebuilds, and when

**Editing `src/` does not change the page.** The page is `dist/`. Refresh
re-reads the folder; it does not build.

The pane covers the user: a built page has a **Rebuild** button beside Refresh,
its output shows while it runs, and **Auto-rebuild** is on by
default — so opening the page rebuilds it first. What that does NOT cover
is the person already looking at the page while you edit it: they will press
Refresh and see the old build. So **rebuild in the same turn as the edit**, and
say that you did.

**`node_modules` is not saved.** The mirror ignores it, deliberately. That costs
nothing at runtime — the page is `dist/`, plain files — but the dependencies are
gone once the sandbox has been recycled. The Rebuild button installs before it
builds, so it heals itself; from a shell, run the install line above first. The
lock is what makes that reproducible, which is why `--frozen-lockfile` is the
command to use: without it two installs from one lock can resolve differently,
and then the lock was pointless.

## When plain files are still right

| | no build | built (this folder) |
|---|---|---|
| edit → see it | change a file, press Refresh | change a file, **rebuild** (or reopen the page), press Refresh |
| what ships | the files you wrote | `dist/`, derived |
| libraries | a UMD file in the folder | `package.json` |
| checking | none | `tsc --noEmit` before every build |
| good for | one screen, no state worth naming | everything else |

The build costs a step that can be forgotten — which is what Auto-rebuild is
for, so it is no longer the reason it once was to avoid one. What it buys is a
compiler, and on a page nobody can debug from the outside that is the trade
worth making.

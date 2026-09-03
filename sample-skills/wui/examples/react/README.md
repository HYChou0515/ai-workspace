# The built example

Copy this folder when hand-written DOM stops paying. Then, in the sandbox:

```sh
pnpm install --frozen-lockfile   # or `pnpm install` the first time, to write the lock
pnpm build                       # → dist/
```

`dist/` is the page. `src/` is what you edit. Both live in the workspace.

## Who rebuilds, and when

**Editing `src/` does not change the page.** The page is `dist/`. Refresh
re-reads the folder; it does not build.

The pane covers the user: a built page has a **Rebuild** button beside Refresh,
its output shows while it runs, and **"Rebuild when I open this"** is on by
default — so opening the page cannot show a stale one. What that does NOT cover
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

## Why not just write DOM

Because at some size you stop being able to. Use whichever you would reach for
outside this platform:

| | no build | built |
|---|---|---|
| edit → see it | change a file, press Refresh | change a file, **rebuild** (or reopen the page), press Refresh |
| what ships | the files you wrote | `dist/`, derived |
| libraries | a UMD file in the folder | `package.json` |
| good for | a form, a table, a dashboard | real state, routing, a component library |

Neither is the recommended one. The build step buys expressiveness and costs a
step that can be forgotten.

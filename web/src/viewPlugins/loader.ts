/**
 * Runtime view plugins, loaded before the first render (#847/#848 PR1 P4).
 *
 * `main.tsx` calls `loadViewPlugins()` right after `import "./ext"` and renders
 * when it settles: the registry is a plain map, so a kind registered after a
 * view painted would not appear in it. Each plugin is an ES module the operator
 * installed; it resolves `react` and `@aiws/view-sdk` to the HOST's copies
 * through the import map (`vite-plugins/sharedModules.ts`) and registers its
 * kinds with `registerViewKind` as it is imported.
 *
 * Nothing here may stop the app: a list that will not load, an SDK major that
 * differs, an `import()` that throws or never settles, a module that registers
 * none of what its manifest declared — each becomes a renderer for that
 * plugin's kinds that throws a message naming the plugin and the reason, so
 * every `*.ai.yaml` using them shows it through the view panel's own error
 * boundary instead of a quiet "Unsupported view kind".
 */
import { apiFetch, API_PREFIX } from "../api/http";
import { hasViewKind, registerViewKind } from "../renderers/entity/viewKindRegistry";
import { SDK_VERSION } from "./sdkVersion";

/** One row of `GET /api/view-plugins`. */
export type ViewPluginInfo = {
  name: string;
  sdk: string;
  kinds: string[];
  /** Relative to the API root. */
  entry_url: string;
};

type Options = {
  list?: () => Promise<ViewPluginInfo[]>;
  importModule?: (url: string) => Promise<unknown>;
  /** For the list request, and for each plugin's import. */
  timeoutMs?: number;
};

async function fetchList(): Promise<ViewPluginInfo[]> {
  const resp = await apiFetch("/view-plugins");
  if (!resp.ok) throw new Error(`GET /api/view-plugins answered ${resp.status}`);
  return (await resp.json()) as ViewPluginInfo[];
}

function importUrl(url: string): Promise<unknown> {
  // Vite must not try to resolve this at build time: it is a runtime URL.
  return import(/* @vite-ignore */ url);
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`timed out after ${ms} ms`)), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e: unknown) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

/** Register `kind` as a renderer that fails loudly, naming the plugin. */
function registerFailure(plugin: string, kind: string, reason: string): void {
  if (hasViewKind(kind)) return; // an incumbent (ext/, another plugin) keeps it
  const message = `view plugin "${plugin}" is unavailable: ${reason}`;
  registerViewKind({
    kind,
    Component: () => {
      throw new Error(message);
    },
    ownsEmptyState: true,
    suppressQuickCreate: true,
  });
}

function reasonOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function loadOne(p: ViewPluginInfo, importModule: (url: string) => Promise<unknown>, timeoutMs: number) {
  const hostMajor = SDK_VERSION.split(".")[0];
  const major = String(p.sdk).split(".")[0];
  if (major !== hostMajor) {
    const why = `it was built for view SDK ${p.sdk}, and this app provides SDK ${SDK_VERSION}`;
    for (const k of p.kinds) registerFailure(p.name, k, why);
    return;
  }
  // Which declared kinds were free before the import: a kind something else
  // already holds is not this plugin's to claim, so its absence afterwards is
  // not a failure of this plugin to register it.
  const free = p.kinds.filter((k) => !hasViewKind(k));
  try {
    await withTimeout(importModule(`${API_PREFIX}${p.entry_url}`), timeoutMs);
  } catch (e) {
    console.error(`view plugin "${p.name}" failed to load`, e);
    for (const k of free) registerFailure(p.name, k, reasonOf(e));
    return;
  }
  for (const k of free) {
    if (!hasViewKind(k)) registerFailure(p.name, k, `it declares view kind "${k}" but did not register it`);
  }
}

/** Load every installed plugin. The first render waits on this, so it is
 * bounded twice over: the list request by `timeoutMs`, and the plugins — loaded
 * side by side, not one after another — each by `timeoutMs` too, so the whole
 * wait is at most about two timeouts however many plugins hang. */
export async function loadViewPlugins(opts: Options = {}): Promise<void> {
  const list = opts.list ?? fetchList;
  const importModule = opts.importModule ?? importUrl;
  const timeoutMs = opts.timeoutMs ?? 15_000;
  let plugins: ViewPluginInfo[];
  try {
    plugins = await withTimeout(list(), timeoutMs);
  } catch (e) {
    console.error("view plugins: could not list them; the app runs without", e);
    return;
  }
  await Promise.allSettled(plugins.map((p) => loadOne(p, importModule, timeoutMs)));
}

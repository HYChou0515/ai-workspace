"""`view_plugin new` — scaffold a buildable runtime view plugin (#847/#848 P11).

Mirrors `workflow new`: files only, each a working starting point, nothing
registered anywhere. The layout is the one `view_plugin build` installs:

    view-plugins/<name>/
      plugin.json
      web/            package.json, vite.config.ts, tsconfig.json, src/{index,View}.tsx
      sandbox-src/    (--with-sandbox) a uv project whose one console script
                      answers the tool 3-stage contract, launched isolated
      skill/SKILL.md  (--with-skill)
      scenarios/      (--with-skill) one should-call, one should-not-call
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _web_files(name: str, kind: str) -> dict[str, str]:
    package = {
        "name": f"aiws-view-plugin-{name}",
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "scripts": {"build": "vite build"},
        "devDependencies": {"@vitejs/plugin-react": "^4.3.0", "vite": "^6.0.0"},
    }
    return {
        "web/package.json": json.dumps(package, indent=2) + "\n",
        "web/vite.config.ts": f"""\
/**
 * The {name} plugin's web build: ONE ES module, `index.js`, with React and the
 * view SDK EXTERNAL — the SPA's import map hands it the host's copies. Bundling
 * either gives the plugin a second React (its hooks throw) or a second registry
 * (its kind never appears); `view_plugin check` refuses both.
 */
import react from "@vitejs/plugin-react";
import {{ defineConfig }} from "vite";

export default defineConfig({{
  plugins: [react()],
  mode: "production",
  // Library mode leaves `process.env.NODE_ENV` in the output, and the browser
  // has no `process`: a bundled library's dev checks would throw on import.
  define: {{ "process.env.NODE_ENV": JSON.stringify("production") }},
  build: {{
    lib: {{ entry: "src/index.tsx", formats: ["es"], fileName: () => "index.js" }},
    rollupOptions: {{
      external: ["react", "react/jsx-runtime", "react-dom", "react-dom/client", "@aiws/view-sdk"],
    }},
    sourcemap: true,
  }},
}});
""",
        "web/tsconfig.json": json.dumps(
            {
                "//": "esbuild's JSX settings only; web/'s tsc type-checks src/ "
                "against the real SDK.",
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "ESNext",
                    "moduleResolution": "bundler",
                    "jsx": "react-jsx",
                    "strict": True,
                    "isolatedModules": True,
                    "skipLibCheck": True,
                },
                "include": ["src"],
            },
            indent=2,
        )
        + "\n",
        "web/src/index.tsx": f"""\
import {{ registerViewKind }} from "@aiws/view-sdk";

import {{ View }} from "./View";

registerViewKind({{ kind: "{kind}", Component: View }});
""",
        "web/src/View.tsx": f"""\
/**
 * `view: {kind}` — reads its own keys with `viewParamString`, and a workspace
 * file with `useFileBuffer`. Everything comes from `@aiws/view-sdk`.
 */
import {{ type EntityViewProps, useFileBuffer, viewParamString }} from "@aiws/view-sdk";

function FromFile({{ path }}: {{ path: string }}) {{
  const {{ entry }} = useFileBuffer(path);
  if (entry.status === "loading") return <p role="status">Loading {{path}}…</p>;
  if (entry.status === "error") {{
    return <p role="status">{{entry.error ?? `could not read ${{path}}`}}</p>;
  }}
  return <pre>{{entry.text.slice(0, 2000)}}</pre>;
}}

export function View({{ spec }}: EntityViewProps) {{
  const source = viewParamString(spec, "source")?.trim() ?? "";
  if (!source) return <p role="status">This view needs a `source:` naming a workspace file.</p>;
  return <FromFile path={{source}} />;
}}
""",
    }


def _sandbox_files(name: str) -> dict[str, str]:
    pkg = name.replace("-", "_") + "_sandbox"
    script = f"{name}-sandbox"
    return {
        "sandbox-src/pyproject.toml": f"""\
[project]
name = "{script}"
version = "0.1.0"
description = "The {name} view plugin's sandbox commands."
requires-python = ">=3.10"
dependencies = []

[project.scripts]
{script} = "{pkg}.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg}"]

# A view plugin's commands are the platform's, run whenever a view opens: they
# import what this bundle ships, never a user's `pip install` (#847/#848).
[tool.workspace-tool]
launch = "isolated"
""",
        f"sandbox-src/src/{pkg}/__init__.py": "",
        f"sandbox-src/src/{pkg}/cli.py": f'''\
"""The {name} plugin's sandbox commands, under the tool 3-stage contract:

    launch                     -> the command list as JSON
    launch <cmd>               -> that command's metadata + JSON schema
    launch <cmd> '<json>'      -> run it

The view's web half calls a command with `useSandboxRun("{name}", cmd, args)`.
`validate` is what `show_file` runs before showing a `view:` file of this
plugin (plugin.json `"validate": true`): exit 0 and print ONE summary line, or
exit non-zero with what to fix on stderr.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

COMMANDS = {{
    "validate": {{
        "name": "validate",
        "description": "Check a view file before it is shown.",
        "params_json_schema": {{
            "type": "object",
            "properties": {{"path": {{"type": "string"}}}},
            "required": ["path"],
        }},
    }},
}}


def validate(args: dict) -> int:
    path = Path(args["path"])
    if not path.is_file():
        print(f"no such view file: {{path}}", file=sys.stderr)
        return 1
    print(f"{{path.name}}: {{path.stat().st_size}} bytes")
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        listing = [{{"name": n, "description": c["description"]}} for n, c in COMMANDS.items()]
        print(json.dumps(listing))
        return
    cmd = COMMANDS.get(argv[0])
    if cmd is None:
        print(f"unknown command {{argv[0]!r}}", file=sys.stderr)
        raise SystemExit(2)
    if len(argv) == 1:
        print(json.dumps(cmd))
        return
    raise SystemExit({{"validate": validate}}[argv[0]](json.loads(argv[1])))
''',
    }


def _skill_files(name: str, kind: str) -> dict[str, str]:
    should = {
        "name": "draws-the-view",
        "note": f"A request this plugin serves: the agent writes a `view: {kind}` file "
        "and shows it.",
        "prompt": f"Show me notes.txt as a {kind} view.",
        "expect": {"must_call": ["write_file", "show_file"]},
        "data": ["notes.txt"],
    }
    should_not = {
        "name": "leaves-unrelated-work-alone",
        "note": "A request with nothing to draw: the plugin's guidance must not pull "
        "the agent into writing a view.",
        "prompt": "What is 17 times 23?",
        "expect": {"must_not_call": ["write_file", "show_file"]},
    }
    return {
        "skill/SKILL.md": f"""\
---
name: {name}
description: Show a workspace file as a live `{kind}` view.
---

To show a file as a `{kind}` view, write a `*.ai.yaml` file:

```yaml
view: {kind}
title: <what the user is looking at>
source: <workspace path of the file>
```

then call `show_file` on that `.ai.yaml` path.
""",
        "scenarios/draws-the-view.json": json.dumps(should, indent=2) + "\n",
        "scenarios/leaves-unrelated-work-alone.json": json.dumps(should_not, indent=2) + "\n",
        "scenarios/notes.txt": "first line\nsecond line\n",
    }


def scaffold_plugin(
    root: Path, name: str, *, with_sandbox: bool = False, with_skill: bool = False
) -> list[Path]:
    """Write `root/<name>/`. Raises `ValueError` on a bad name or an existing folder."""
    if not _NAME.match(name):
        raise ValueError(f"plugin name {name!r} must be a lowercase slug ([a-z0-9][a-z0-9_-]*)")
    dest = root / name
    if dest.exists():
        raise ValueError(f"{dest} already exists")
    kind = name
    manifest: dict = {
        "name": name,
        "sdk": "1",
        "kinds": [kind],
        "views": [{"kind": kind, "when": "a workspace file the user wants to look at"}],
    }
    files = _web_files(name, kind)
    if with_sandbox:
        manifest["sandbox"] = {"bundle": "sandbox", "validate": True}
        files |= _sandbox_files(name)
    if with_skill:
        manifest["skill"] = "skill"
        files |= _skill_files(name, kind)
    files["plugin.json"] = json.dumps(manifest, indent=2) + "\n"
    written = []
    for rel, text in sorted(files.items()):
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    if with_sandbox:
        # `view_plugin build` prebuilds from a frozen lockfile; a project with no
        # dependencies locks offline.
        subprocess.run(["uv", "lock", "--directory", str(dest / "sandbox-src")], check=True)
        written.append(dest / "sandbox-src" / "uv.lock")
    return written

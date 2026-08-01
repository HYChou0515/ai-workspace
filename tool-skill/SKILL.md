---
name: install-workspace-tool
description: Install a tool published for the AI workspace platform as an MCP server, given the URL of its GitLab repository. Use when someone wants to call one of that platform's tools from this editor, or says a tool is "not available here". Also covers diagnosing an installed one that has stopped working.
---

# Install a workspace tool

Someone gives you the URL of a GitLab repository that publishes a tool, and
wants to use that tool here. Everything else — what the tool is called, where
its bytes live, how to run it — you work out or already have below.

One image runs every tool; the tool arrives by URL. So installing one is
writing a config entry and proving it answers.

---

## What the platform team filled in

    RUNNER_IMAGE = <<RUNNER_IMAGE>>

**If that still reads `<<RUNNER_IMAGE>>`, stop.** This copy of the skill was
handed out before the platform team set it, and there is nothing to run. Say
so and ask them for the image address — do not guess one, and do not go
looking for a substitute on a registry.

---

## 1. Find what the repository publishes

Its CI publishes two files as build artifacts: `dist/tool.manifest.json` and
`dist/tool.tar.gz`. You want the URL of the first one.

From a repo URL like `https://gitlab.example/rca/wafer-history`, the manifest
of the latest build on the default branch is:

    https://gitlab.example/api/v4/projects/rca%2Fwafer-history/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool

Three things to get right:

- The project path is **URL-encoded** (`/` → `%2F`).
- The ref is the repository's **default branch**. Check it rather than
  assuming `main`; the CI only publishes from the default branch.
- The job is named `build-tool` in the template every author starts from. If
  a repo renamed it, read their `.gitlab-ci.yml`.

Fetch that URL. A private GitLab needs a header — `PRIVATE-TOKEN: <token>`.

What comes back tells you the rest:

```json
{ "name": "wafer-history", "version": "1.4.2", "commands": [ … ],
  "grant": "eyJtYXhfYnl0ZXMiOjE1…" }
```

`grant` is the platform's certificate, and it is what says which tool this is.
Its first half is base64 JSON — decode it for the name to use:

```sh
python3 -c 'import base64,json,sys
p = sys.argv[1].split(".")[0]
print(json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))["tool"])' '<the grant value>'
```

**That name** — not the manifest's own `name`, and not the repository's.
Identity comes from the certificate, which is why two authors may both publish
a tool whose command is called `data-fetch` and neither shadows the other. The
runner checks the name you pass against the certificate, so the manifest's
`name` will not do.

No `grant` field at all means the tool has not been admitted to the platform;
see **When it does not work**.

If the fetch fails, go to **When it does not work** below rather than trying
a different URL shape.

## 2. Write the config

Add one server entry, in the form this editor uses. The command and its
arguments are the same everywhere; only the surrounding file differs.

```json
{
  "mcpServers": {
    "wafer-history": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "${PWD}:/work",
        "-v", "mcp-tools:/cache",
        "-e", "TOOL_ARTIFACT_TOKEN",
        "<<RUNNER_IMAGE>>",
        "wafer-history",
        "https://gitlab.example/api/v4/projects/rca%2Fwafer-history/jobs/artifacts/main/raw/dist/tool.manifest.json?job=build-tool"
      ]
    }
  }
}
```

Every part of that earns its place:

- `-i` — the transport is this process's stdin and stdout.
- `-v ${PWD}:/work` — the tool resolves relative paths against the working
  directory, which is the user's project. **Without it, a tool that writes a
  file appears to succeed and the file goes with the container.**
- `-v mcp-tools:/cache` — bundles are stored by content hash, so a version is
  downloaded once instead of on every start. Use a named volume, not a host
  directory. Omitting it works and simply re-downloads each start.
- `-e TOOL_ARTIFACT_TOKEN` — passes the variable through when the artifact
  store is private. Harmless when unset.

Add **nothing about this machine** — no `--user`, no uid, no absolute home
path. The runner takes the ownership of the mounted directory and becomes it,
so files the tool writes belong to the person running it. A uid written here
would be wrong on the next machine this config is copied to.

## 3. Prove it answers

Do not report success from having written a file. Run it:

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | docker run --rm -i -v mcp-tools:/cache -v "$PWD:/work" -e TOOL_ARTIFACT_TOKEN \
      <<RUNNER_IMAGE>> wafer-history '<the manifest URL>'
```

A working install prints one line to stderr naming the tool and version, then
a JSON-RPC reply on stdout listing the commands. The first run also downloads
the bundle, so give it time.

Then reload this editor's MCP servers so the new entry is picked up, and tell
the person which commands they now have — by name, from the reply.

---

## When it does not work

Read the failure before acting on it. The three parties who can fix things
are different people, and sending someone to the wrong one costs them a day.

### The manifest URL will not fetch

| What you see | What it means | Who fixes it |
|---|---|---|
| `404` | **Two causes that look identical.** The artifact expired (GitLab discards them ~30 days after the build unless the job sets `expire_in: never`), or the account has no access to that project — GitLab answers 404 for a project you may not see, rather than admitting it exists. | Ask the person to open the URL in their browser. It loads ⇒ expiry, and the **tool's author** must fix their CI. It does not ⇒ access, and the **platform team** grants it. |
| `401` | The project is private and no token was sent. | **The person**: create a GitLab read token and set `TOOL_ARTIFACT_TOKEN` in the environment this editor runs in. |
| `401` … `the artifact credential was NOT sent: <host> is not in TOOL_ARTIFACT_HOSTS` | The token exists, and the runner declined to send it to that host. The runner only sends it to the artifact store the platform published it for. | **Check the URL first** — this is what a wrong host looks like, and it is the one failure that means somebody may be trying to collect the token. If the host is genuinely right, **the platform team** adds it to the image. |
| `403` | Authenticated, but not allowed this project. | **The platform team.** |
| The URL 404s but the repo browses fine, and `dist/` has never existed | That repository has never published a successful build, or its job is not called `build-tool`. | **The tool's author.** Read their `.gitlab-ci.yml` and say which. |

Do not work around a 404 by pointing at an older job id. A pinned artifact
stops receiving the author's fixes, and the person will be running a version
nobody else has months later.

### It fetches, but the runner refuses it

| What you see | What it means | Who fixes it |
|---|---|---|
| `carries no certificate` | Every tool needs one the platform signed. This one has not been admitted — or the author has not committed the certificate they were given as `tool-certificate.token`. | **The platform team** if it was never issued; **the tool's author** if it was. Ask which. |
| `this certificate was issued for '<other>'` | The certificate in that artifact admits a different tool than the name you were given. | **The platform team** — either the name is wrong or the wrong certificate was committed. |
| `is good for artifacts under <X>, and this one came from <Y>` | The certificate belongs to a tool published somewhere else. Certificates are public, so this is what a copied one looks like. | **Stop and say so.** Do not try another URL. Whoever gave you this one is pointing at something that is not the tool it claims to be. |
| `refused: … builder …` | The bundle was built against a different base than this runner. Its interpreter and compiled dependencies will not load here. | **The tool's author** rebuilds with the current builder image. There is no flag for this and you must not look for one — the alternative to refusing is a segmentation fault inside their tool. |
| `refused: … sha256 …` | The bytes fetched are not the bytes the manifest describes. | Retry once; a truncated download does this. If it repeats, the **tool's author** has a broken publish. |
| `this certificate was issued for '<other tool>'` | The size certificate in that repo belongs to a different tool. | **The tool's author.** |
| `the bundle is …MB and the limit for … is …MB` | The tool is over the platform's size limit and has no certificate raising it. | **The tool's author**, who can ask the platform team to review it. |

### It starts, but behaves oddly

| What you see | What it means | Who fixes it |
|---|---|---|
| `warning: nothing is mounted at /work` | The config is missing `-v ${PWD}:/work`. Reads fail loudly; **writes succeed and vanish**. | **You** — add it. |
| `cannot run …` on a machine that worked yesterday | The runner never serves a copy it could not confirm today, so this is a fetch failure, not a corrupted cache. Read the rest of the message. | See the fetch table above. |
| `docker: command not found`, or the daemon is not running | — | **The person.** |
| The tool is listed, but the agent never calls it | Its description does not say when it should be used. | **The tool's author** — pass on which tool and what the person was trying to do. |

### Things not to do

- Do not edit the bundle, or extract it and run it directly. It is verified
  on the way in; a copy that skipped that is a copy nobody checked.
- Do not add `--user`, a uid, or a host path to the config. Anything
  machine-specific breaks the next person who copies it.
- Do not substitute a different image for `RUNNER_IMAGE`. It is the platform's
  and is what a tool's compiled dependencies were built to run against. It is
  also what decides where the artifact credential may be sent, so another
  image is another answer to that question.
- Do not set `TOOL_ARTIFACT_TOKEN` to get past a refusal you do not
  understand. A refusal that mentions the certificate is not about
  credentials, and a token sent somewhere unexpected does not come back.
- Do not report an install as done without step 3.

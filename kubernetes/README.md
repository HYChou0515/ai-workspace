# Kubernetes deploy

Kustomize layout:

```
kubernetes/
  base/                 deployment · service · configmap · ingress · pvc · kustomization
  overlays/
    staging/            namespace rca-staging, host staging.company.com, in-memory store
    production/         namespace rca-prod,    host company.com,         specstar store
```

Both overlays deploy under the **sub-path `/my-svc/rca`** (see "Sub-path" below).

## Build + push the image

```bash
# root deploy (served at /)
docker build -t <registry>/rca-app:TAG -f docker/Dockerfile .

# sub-path deploy (served at /my-svc/rca) — the base path is BAKED into the SPA
docker build -t <registry>/rca-app:TAG --build-arg BASE_PATH=/my-svc/rca/ -f docker/Dockerfile .

docker push <registry>/rca-app:TAG
```

Point the overlay at your registry image (the overlays pin `rca-app:staging` /
`rca-app:1.0.0` by name):

```bash
cd kubernetes/overlays/production
kustomize edit set image rca-app=<registry>/rca-app:1.0.0
```

## Deploy

```bash
kubectl apply -k kubernetes/overlays/staging
kubectl apply -k kubernetes/overlays/production
```

(`kubectl apply -k` uses the built-in kustomize. Create the namespace first if it
doesn't exist: `kubectl create ns rca-prod`.)

## Sub-path (`company.com/my-svc/rca`)

Three pieces must agree on the prefix:

1. **Image build** — `--build-arg BASE_PATH=/my-svc/rca/` bakes the SPA's asset
   URLs, the router basename, and the API fetch prefix (all from Vite's
   `BASE_URL`). Trailing slash required.
2. **Ingress** — strips the prefix so the backend sees `/` (the overlays use the
   nginx `rewrite-target: /$2` + `use-regex` with `path: /my-svc/rca(/|$)(.*)`).
3. **`APP_ROOT_PATH`** (configmap) — `/my-svc/rca`, so generated URLs
   (OpenAPI/docs) include the prefix. The overlays set this.

For a **root** deploy: build with `BASE_PATH=/` (default), set `APP_ROOT_PATH=""`,
and use a plain `path: /` ingress (as in `base/ingress.yaml`).

## Ollama (KB embeddings + chat model)

The app reaches Ollama via `OLLAMA_API_BASE` (configmap, default
`http://ollama:11434`). Run Ollama as its own Deployment + Service `ollama` in
the same namespace (with its own PVC for pulled models), or point
`OLLAMA_API_BASE` at an external endpoint. Pull the models the configmap names:
`bge-m3` (embeddings, 1024-dim → must match `KB_EMBED_DIM`) and `qwen3:14b`
(KB agent + retrieval). Without a reachable embedder, KB ingest/search fail.

## Persistence

The PVC (`rca-data`, ReadWriteOnce) mounts at `/data`; `SANDBOX_ROOT=/data/sandbox`
persists the agent's workspace files. `FILESTORE_KIND=specstar` (production) keeps
investigations/conversations/KB on specstar — point its data directory at `/data`
per the specstar backend you use; staging uses the volatile `memory` store.

Because state is per-pod on a ReadWriteOnce volume, the Deployment runs **one
replica** with the `Recreate` strategy — it does not horizontally scale as-is.

## Job workers (#312)

The base splits the job runner out of the API. `deployment.yaml` (`rca-app`)
serves HTTP and **enqueues** jobs but, with `RUN_CONSUMERS="false"` (configmap),
does NOT drain the queues. `workers.yaml` adds one Deployment per JobType —
`rca-worker-{index,wiki,card-gen,sanity,eval,graph}`, each running
`python -m workspace_app.worker <jobtype>` — so a heavy embed backlog scales the
**index** workers (HPA, CPU-target) without touching the API. `wiki`/`card-gen`
also get HPAs (but are IO-bound on the LLM, so tune min/max replicas rather than
trusting the CPU target — KEDA queue-depth scaling is out of scope); `sanity`,
`eval` and `graph` are fixed 1-replica Deployments (infrequent — `eval` is
enqueued nightly by `cronjob-eval.yaml` #535, `graph` weekly by
`cronjob-graph.yaml` #534). Workers trap SIGTERM and
drain in-flight work (`terminationGracePeriodSeconds: 60`); pending jobs are
durable, so a killed pod's work is redelivered.

**A split needs a SHARED backend.** The base configmap defaults to
`FILESTORE_KIND=memory` + the in-memory specstar backend, which isolates each
pod — the API's queue and a worker's queue would be different objects and the
worker would drain nothing. For a real split, point every pod at one **Postgres**
specstar backend (and optionally `message_queue.kind: rabbitmq`) via your
config.yaml, so producer + workers share one queue.

**All-in-one instead?** Drop `workers.yaml` from `base/kustomization.yaml` and set
`RUN_CONSUMERS="true"` — `rca-app` then both serves HTTP and consumes in-process
(the pre-#312 behaviour), which is fine for a single-pod / low-volume deploy.

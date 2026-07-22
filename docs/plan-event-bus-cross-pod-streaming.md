# Plan — RabbitMQ cross-pod event bus (live SSE streaming without sticky)

## Problem

A turn's live SSE events exist ONLY in the memory of the pod running the turn:
`_ws_sessions[key]` (the in-memory subscriber queues in `api/turns.py`) plus the
per-pod `#601` replay buffer. No other pod has them. So when sticky routing is
degraded (round-robin), a viewer whose SSE lands on a pod that isn't running the
turn is **blind for the whole turn** — the "還在準備 / no streaming" symptom.
`#601` only rescues a SAME-pod reconnect; it is per-pod, so cross-pod is still
blind.

The only thing "held only by the origin pod" is the live event broadcast. To make
streaming survive landing on any pod, those events must become reachable from
other pods. Production has **RabbitMQ + Postgres + PVC**. The right tool for
*ephemeral real-time fan-out* is RabbitMQ pub/sub (Postgres/PVC are for durable
storage — the turn RESULT is already persisted there at turn-end; live progress is
ephemeral and does not need durability).

## Goal

Turn events fan out to every pod, so a viewer's SSE streams **regardless of which
pod runs the turn** — killing the cross-pod-blind symptom and, as a bonus, fixing
cross-pod multi-viewer collaboration (`#43`/`#455`: two people on different
machines watching one chat both stream).

Non-goals: cross-pod reconnect-replay of the exact disconnect-gap events (needs a
globally-coherent seq — a heavy shared per-event counter or a per-turn seq
rework); the `seq` model is **untouched**.

## Locked design (from /grill-me)

### Abstraction — a new `IEventBus`, mirroring `ITurnControl`

- `IEventBus` (abc.ABC, I-named; interface + impls in separate modules).
- `InMemoryEventBus` — the **default** (single pod / tests, zero infra).
- `RabbitMQEventBus` — fanout over the broker (multipod), aio_pika.
- Injected into `ChatTurnEngine` by `create_app`, exactly like `SpecstarTurnControl`
  / `InMemoryTurnControl`.

**Interface:**
```python
class IEventBus(ABC):
    def publish(self, key: str, origin: str, event: AgentEvent) -> None: ...  # fire-and-forget
    def start_consuming(self, on_event: Callable[[str, str, AgentEvent], None]) -> None: ...
```
`ChatTurnEngine` calls `bus.start_consuming(self._on_bus_event)` once at startup;
`_on_bus_event(key, origin, event)` does skip-own + demux to `_ws_sessions[key]`.

### Integration — dual path (local ALWAYS + bus for other pods)

- `_WorkspaceSession.publish(event)`: deliver to **local** subscribers (unchanged —
  same-pod is instant and does NOT depend on RabbitMQ) **and** `bus.publish(key,
  THIS_POD_ID, event)`.
- Each pod runs **one** bus consumer (`_on_bus_event`) that, per event:
  1. **skip-own-origin**: `origin == THIS_POD_ID` → skip (already delivered locally).
  2. demux by `key` → `session = _ws_sessions.get(key)`; no session / no subscribers
     → discard (nobody is listening here).
  3. `session.deliver_from_bus(event)` — assign this pod's `seq`, append to the
     `#601` buffer, fan out to local subscriber queues. **Never re-publishes to the
     bus** (that is what prevents a fan-out storm/loop).

`THIS_POD_ID` is a per-process id minted at engine construction.

### RabbitMQ topology (impl detail of `RabbitMQEventBus`, behind the interface)

- **fanout exchange** + **one exclusive, auto-delete, server-named queue per pod**
  bound to it; one demux consumer reads that queue. Every pod receives every event
  and filters locally by "do I have a subscriber for this key?" (stateless — no
  per-key bindings to leak/miss).
- Code comment records the future alternative: **topic exchange + per-key bindings**
  (a pod only receives keys it views) — switch to it if broker bandwidth becomes a
  bottleneck; nothing outside `RabbitMQEventBus` changes.
- Queue is **transient** (non-durable) with a **`queue_max_length`** bound
  (backpressure → drop oldest). Live events are ephemeral; the store is the durable
  backstop for the final result.
- **RobustConnection** (aio_pika) auto-reconnects; on reconnect, re-declare the queue
  + re-consume.

### seq — untouched (`#601` stays per-pod)

- Same-pod reconnect replay: unchanged (`#601` buffer, per-pod seq).
- Cross-pod reconnect: streaming **resumes** (the new pod's bus consumer delivers
  live events going forward); only the ~1s disconnect-gap events are not precisely
  replayed (per-pod seq mismatch), and the final result is recovered by re-hydrate
  at turn-end. No worse than today for that narrow edge; live streaming is the win.

### Fallback — RabbitMQ is never a hard dependency

- Bus **connects in the background** at startup (RobustConnection). RabbitMQ down at
  boot ⇒ server still starts, in local-only mode, upgrading to cross-pod once
  connected.
- `publish` to the bus is **fire-and-forget** and **never blocks the turn**: local
  delivery happens first (synchronous); the bus send is handed off (a background
  publisher), and a failure is swallowed + logged (ephemeral events are not
  retried). The turn's latency is independent of RabbitMQ.
- Bus down/degraded ⇒ cross-pod viewers fall back to the existing `#202` store-poll
  (final result at turn-end) — the SAME degradation as today's broken-sticky
  cross-pod. Same-pod always works. No crashes, no lost final results.

### Config

New `event_bus` block (mirrors `message_queue`), default `memory` (opt-in):
```python
@dataclass(frozen=True)
class EventBusSettings:
    kind: str = "memory"            # memory | rabbitmq
    url: str = ""                   # amqp url; empty → reuse message_queue.rabbitmq.url
    exchange: str = "rca_turn_events"
    queue_max_length: int = 10_000  # per-pod queue bound (drop oldest under backpressure)
```
- `Settings.event_bus`; `get_event_bus(settings)` factory (like
  `build_message_queue_factory`): `memory` → `InMemoryEventBus`; `rabbitmq` →
  `RabbitMQEventBus(url or message_queue.rabbitmq.url, exchange, queue_max_length,
  heartbeat=message_queue.rabbitmq.heartbeat_seconds)`.
- **Credentials**: embedded in the AMQP url (`amqp://user:pass@host/vhost`), sourced
  from `${RABBITMQ_URL}` (k8s secret via config `${ENV}` interpolation) — the same
  url the job queue already uses. **Zero new secret handling.** `config.example.yaml`
  documents it; the real url lives in the deployment's secret config, never committed.
- **Default `memory` = zero side effect**: existing deployments (no `event_bus`
  config) behave exactly as today (same-pod only), including existing multipod
  (still cross-pod-blind) until they opt in with `kind: rabbitmq`.

## Edge cases and how each is handled

1. **Self-delivery** (fanout copies to the origin's own queue) → **skip-own-origin**.
2. **Re-publish loop** (a bus event routed back through `publish` would storm) →
   bus-received events go through a **separate `deliver_from_bus`** that never sends
   to the bus. The single most dangerous bug to get wrong — explicitly tested.
3. **Slow/stuck consumer → unbounded queue** → **`queue_max_length` + transient
   messages** (drop oldest). Ephemeral, store backstops the result.
4. **RabbitMQ blip** (connection drop → queue gone → re-created) → cross-pod gap for
   those seconds → degrade to store recovery. Rare, acceptable.
5. **Concurrent same-key cross-pod** (two turns on one chat on two pods) → interleaved
   output — a pre-existing weirdness of two concurrent turns on one chat, not new to
   the bus. Accepted.

## Definition of Done (verify against these)

- [ ] Two `ChatTurnEngine`s sharing ONE `InMemoryEventBus` (= two pods): a turn on
      engine A streams to a viewer subscribed on engine B (cross-pod delivery).
- [ ] Multi-chat isolation: two keys on the bus never cross-talk.
- [ ] Multi-viewer same chat, different pods: both stream.
- [ ] skip-own: a same-pod viewer gets each event exactly once (never doubled).
- [ ] No re-publish loop: a bus-delivered event is not re-sent to the bus.
- [ ] Single-pod / existing tests: `InMemoryEventBus` default is a no-op (skip-own),
      behavior unchanged; existing suite untouched.
- [ ] `publish` never blocks/crashes a turn when the bus is down; degrades to local +
      store-poll.
- [ ] Config: `event_bus.kind` default `memory` (unchanged); `rabbitmq` selects
      `RabbitMQEventBus`; url reuses `message_queue.rabbitmq.url` when unset.
- [ ] whole-project `ty` clean; `ruff check` + `format --check` clean.
- [ ] 100% coverage gate (full local suite); CI unit suite green. `RabbitMQEventBus`
      real-broker behavior is an `@pytest.mark.integration` test (unit tests use
      `InMemoryEventBus`).

## TDD phases (flat integer; commit per phase)

- **P1 — `IEventBus` + `InMemoryEventBus` + wire into `ChatTurnEngine`.** publish →
  local + bus; `start_consuming` → `_on_bus_event` (skip-own + demux +
  `deliver_from_bus` with NO re-publish). Tests (two engines / one `InMemoryEventBus`):
  cross-pod delivery; skip-own once; multi-chat isolation; multi-viewer cross-pod;
  no re-publish loop; single-pod no-op.
- **P2 — `RabbitMQEventBus`** (aio_pika): fanout exchange + per-pod exclusive
  auto-delete queue + demux consumer + RobustConnection reconnect + fire-and-forget
  publish + `queue_max_length` + transient. Unit tests with a faked aio_pika seam;
  a root/broker-gated `@pytest.mark.integration` test against a real RabbitMQ.
  Fallback: publish swallows errors when down.
- **P3 — config + wiring.** `EventBusSettings` + `get_event_bus` factory +
  `create_app`/`__main__` wiring + `config.example.yaml`. Tests: default memory; the
  knob selects the impl; url reuse.
- **P4 — polish.** Whole-project `ty`, `ruff`, 100% gate. (FE needs nothing — it
  already renders whatever SSE delivers; when cross-pod streaming works, "還在準備"
  cross-pod blindness disappears. A small "還在準備" relabel for the RabbitMQ-down
  degraded case is optional and tracked separately.)

## Key files

`src/workspace_app/api/turns.py` (`_WorkspaceSession.publish`/`deliver_from_bus`,
`ChatTurnEngine._on_bus_event`/wiring), a new `src/workspace_app/api/event_bus/`
package (`base.py` `IEventBus`, `memory.py`, `rabbitmq.py`), `api/app.py` +
`factories.py` + `config/schema.py` + `__main__.py` + `configs/config.example.yaml`.

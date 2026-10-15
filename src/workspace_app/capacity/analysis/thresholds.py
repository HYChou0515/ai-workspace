"""At what load does it get slow (docs/plan-capacity-usage.md §9.8, D13).

Each pair is (slowness per slot, load per slot); a knee in it is the safe
threshold for the line whose load it is:

- a: p95 of general requests (streams excluded), from the latency buckets,
  against API CPU ÷ capacity — sets api.cpu;
- b: p95 of first output − acceptance, against API CPU ÷ capacity — api.cpu;
  and, per model, its turns' wait against that model's requests in flight —
  that model's capacity, beside its first-token pair;
- c: seconds per generated token (1 ÷ tokens/s), against a model's requests in
  flight — shown, sets nothing;
- d: share of sandbox time spent waiting for a CPU the machine did not have —
  pressure minus the time each sandbox was throttled at its own `cpu.max`,
  which the kernel counts as pressure too (E15) — against the sandbox host's
  CPU ÷ capacity — sandbox-host.cpu. The throttled share rides along as its
  own series; without PSI on the kernel there is nothing to fit, and the pair
  says so (`note: no_psi`) rather than "never slowed";
- e: p95 queue wait of one job type's jobs, against the CPU ÷ capacity of
  the workers that run it — that worker line (one pair per job type, §9.8);
- gpu: p95 time to first token, against a model's requests in flight — that
  model's capacity.

A line's threshold is the lowest knee among the pairs that set it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..records import LATENCY_BOUNDS_MS
from .knee import Knee, fit_knee
from .lines import Line
from .observe import Events, Observations

#: The latency buckets' edges in ms; the last, open bucket is read as 60 s.
_EDGES = np.array([0.0, *LATENCY_BOUNDS_MS, 60_000.0])
#: A slot needs this many events before its percentile means anything.
MIN_EVENTS = 3
#: A pair whose load never moved this much has nothing to say about a knee.
MIN_SPAN = 0.2
#: Points sent to the page per pair.
MAX_POINTS = 1500


@dataclass
class Pair:
    id: str
    x_label: str
    y_label: str
    x: np.ndarray
    y: np.ndarray
    knee: Knee | None
    sets: str | None  # the line whose threshold (or capacity) it decides
    model: str = ""
    #: why the pair has nothing to show, when that is not "it never slowed"
    note: str | None = None
    #: a second slowness on the same load, shown beside the first, fitted never
    throttled: np.ndarray | None = None
    #: (e) only: the job type whose queue it is ("" when the workers are one line)
    job: str = ""

    @property
    def span(self) -> float:
        ok = np.isfinite(self.x) & np.isfinite(self.y)
        return float(self.x[ok].max() - self.x[ok].min()) if ok.sum() > 1 else 0.0

    def points(self) -> list[list[float]]:
        return _points(self.x, self.y)


def _points(x: np.ndarray, y: np.ndarray) -> list[list[float]]:
    ok = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if len(ok) > MAX_POINTS:
        ok = np.sort(np.random.default_rng(0).choice(ok, MAX_POINTS, replace=False))
    return [[round(float(x[i]), 5), round(float(y[i]), 5)] for i in ok]


def hist_p95(hist: np.ndarray) -> np.ndarray:
    """[slot, bucket] request counts → each slot's p95 latency (ms), linear
    within its bucket; NaN where a slot has fewer than `MIN_EVENTS`."""
    totals = hist.sum(axis=1)
    out = np.full(hist.shape[0], np.nan)
    cum = np.cumsum(hist, axis=1)
    for s in np.flatnonzero(totals >= MIN_EVENTS):
        target = 0.95 * totals[s]
        b = int(np.searchsorted(cum[s], target))
        below = cum[s, b - 1] if b else 0.0
        inside = hist[s, b]
        frac = (target - below) / inside if inside else 0.0
        out[s] = _EDGES[b] + frac * (_EDGES[b + 1] - _EDGES[b])
    return out


def _p95_by_slot(n: int, events: list[tuple[int, float]]) -> np.ndarray:
    by: dict[int, list[float]] = {}
    for slot, value in events:
        by.setdefault(slot, []).append(value)
    out = np.full(n, np.nan)
    for slot, values in by.items():
        if len(values) >= MIN_EVENTS:
            out[slot] = float(np.quantile(values, 0.95))
    return out


def same_model(asked: str, logged: str) -> bool:
    """Whether a turn's model (as the platform asked for it, `openai/qwen3`)
    is the model LiteLLM logged (`qwen3`, the name the request reached it
    by): the same name, or one is the other behind a `provider/` prefix."""
    if not asked or not logged:
        return False
    return asked == logged or asked.endswith("/" + logged) or logged.endswith("/" + asked)


def _events(p: Any, events: Events, keep: np.ndarray | None = None) -> list[tuple[int, float]]:
    """(slot, wait) of the records that have both stamps and begin in the period."""
    at, wait = events.at_ms, events.wait_s
    ok = np.isfinite(wait) & (at >= p.start_ms) & (at < p.end_ms)
    if keep is not None:
        ok &= keep
    slots = (at[ok] - p.start_ms) // p.step_ms
    return list(zip(slots.tolist(), wait[ok].tolist(), strict=True))


def _util(line: Line | None) -> np.ndarray | None:
    if line is None or not line.capacity:
        return None
    return line.total / line.capacity


def build_pairs(obs: Observations, lines: Mapping[str, Line]) -> list[Pair]:
    p = obs.period
    pairs: list[Pair] = []
    api = _util(lines.get("api.cpu"))
    if api is not None:
        hist = obs.hist.get("api")
        if hist is None:
            hist = np.zeros((p.n, len(_EDGES) - 1))
        pairs.append(Pair("a", "api.util", "latency_p95_ms", api, hist_p95(hist), None, "api.cpu"))
        events = _events(p, obs.turns)
        pairs.append(
            Pair(
                "b",
                "api.util",
                "first_output_p95_s",
                api,
                _p95_by_slot(p.n, events),
                None,
                "api.cpu",
            )
        )
    for line_id, line in lines.items():
        if line.component != "gpu" or line.unit != "inflight":
            continue
        names = obs.turns.names
        theirs = [i for i, name in enumerate(names) if same_model(name, line.model)]
        waits = _events(p, obs.turns, np.isin(obs.turns.label, theirs)) if theirs else []
        if waits:
            pairs.append(
                Pair(
                    "b",
                    "gpu.inflight",
                    "first_output_p95_s",
                    line.total,
                    _p95_by_slot(p.n, waits),
                    None,
                    line_id,
                    line.model,
                )
            )
        latency = obs.latency.get(line.model)
        if latency is None:
            continue
        tps = latency["tps_p50"]
        with np.errstate(divide="ignore", invalid="ignore"):
            per_token = np.where(tps > 0, 1.0 / tps, np.nan)
        pairs.append(
            Pair(
                "c",
                "gpu.inflight",
                "seconds_per_token",
                line.total,
                per_token,
                None,
                None,
                line.model,
            )
        )
        pairs.append(
            Pair(
                "gpu",
                "gpu.inflight",
                "ttft_p95_s",
                line.total,
                latency["ttft_p95"],
                None,
                line_id,
                line.model,
            )
        )
    host = _util(lines.get("sandbox-host.cpu"))
    if host is not None and obs.sandbox_user:
        stalled = sum((m["cpu_psi_s"] for m in obs.sandbox_user.values()), np.zeros(p.n))
        throttled = sum((m["thr_s"] for m in obs.sandbox_user.values()), np.zeros(p.n))
        live = sum((h["live_s"] for h in obs.host.values()), np.zeros(p.n))
        with np.errstate(divide="ignore", invalid="ignore"):
            waited = np.clip(stalled - throttled, 0.0, None)
            share = np.where(live > 0, waited / live, np.nan)
            held = np.where(live > 0, throttled / live, np.nan)
        if not obs.psi_seen:
            share = np.full(p.n, np.nan)
        d = Pair("d", "sandbox_host.util", "cpu_wait_share", host, share, None, "sandbox-host.cpu")
        d.throttled = held
        d.note = None if obs.psi_seen else "no_psi"
        pairs.append(d)
    for line in lines.values():
        if line.component != "worker" or line.resource != "cpu" or line.job == "other":
            continue
        worker = _util(line)
        if worker is None or not len(obs.jobs):
            continue
        keep = None
        if line.job:
            kind = obs.jobs.index_of(line.job)
            keep = obs.jobs.label == (-1 if kind is None else kind)
        events = _events(p, obs.jobs, keep)
        e = Pair(
            "e", "worker.util", "queue_wait_p95_s", worker, _p95_by_slot(p.n, events), None, line.id
        )
        e.job = line.job
        pairs.append(e)
    for pair in pairs:
        if pair.span >= MIN_SPAN or pair.x_label == "gpu.inflight":
            pair.knee = fit_knee(pair.x, pair.y)
    return pairs


def pair_json(pair: Pair) -> dict[str, Any]:
    ok = np.isfinite(pair.x) & np.isfinite(pair.y)
    return {
        "id": pair.id,
        "model": pair.model,
        "x": pair.x_label,
        "y": pair.y_label,
        "sets": pair.sets,
        "points": pair.points(),
        "throttled": None if pair.throttled is None else _points(pair.x, pair.throttled),
        "note": pair.note,
        "job": pair.job,
        "x_max": float(pair.x[ok].max()) if ok.any() else None,
        "span": pair.span,
        "knee": None
        if pair.knee is None
        else {
            "at": pair.knee.at,
            "slope_before": pair.knee.slope_before,
            "slope_after": pair.knee.slope_after,
            "gain": pair.knee.gain,
        },
    }

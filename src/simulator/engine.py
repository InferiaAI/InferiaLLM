"""Discrete-event loop.

One heap of events ordered by (time, kind, sequence number). Kind orders
simultaneous events (arrivals, then step completions, then kicks that start an
idle replica); the sequence number breaks remaining ties in insertion order, so a run is a pure function of the scenario
and the seed. There is one event per engine iteration (forward pass) of a
replica, not per token, plus one per arrival; arrivals are generated lazily so
memory does not grow with the length of the run.
"""

from __future__ import annotations

import heapq
import itertools
import random
from dataclasses import dataclass, field
from typing import Iterable, Optional

from simulator.config import Scenario, place_replicas
from simulator.hardware import get_gpu, get_model
from simulator.metrics import LogHistogram, WeightedBins, overlap
from simulator.scheduler import Replica, Step
from simulator.workload import Request, WorkloadGenerator

# Event kinds double as the tie-break at equal times: every arrival and step
# completion at time t is processed before an idle replica's kick at t, so
# requests arriving at the same instant join the same first step.
_ARRIVAL = 0
_STEP_DONE = 1
_KICK = 2
_AUTOSCALE = 3
_SCALE_READY = 4
_RETRY = 5
_FAILURE = 6
_RECOVER = 7


@dataclass
class ReplicaStats:
    """Per-replica time-weighted measurements inside the measurement window."""

    kv_util: WeightedBins = field(default_factory=WeightedBins)
    waste_ratio: WeightedBins = field(default_factory=WeightedBins)
    ext_frag: WeightedBins = field(default_factory=WeightedBins)
    # Inside the window: time the step is compute- or memory-bound (excludes fixed
    # overhead, TP communication and swap copies), and achieved fractions of peak
    # FLOPs and of HBM bandwidth, integrated over time.
    bound_s: float = 0.0
    flops_s: float = 0.0
    hbm_s: float = 0.0
    output_tokens: int = 0
    last_end: float = 0.0


@dataclass
class SimResult:
    scenario: Scenario
    seed: int
    replicas: list[Replica]
    stats: list[ReplicaStats]
    records: list[Request]
    window: tuple[float, float]
    end_time: float
    itl: LogHistogram
    in_system_integral_full: float
    in_system_integral_window: float
    invariant_checks: int


class Simulation:
    def __init__(
        self,
        scenario: Scenario,
        seed: Optional[int] = None,
        check_invariants: bool = False,
        requests: Optional[Iterable[Request]] = None,
    ):
        self.scenario = scenario
        self.seed = scenario.run.seed if seed is None else seed
        self.check = check_invariants
        self._requests = requests
        self.replicas: list[Replica] = []
        self._by_deployment: dict[str, list[Replica]] = {}
        self._session_replicas: dict[tuple[str, str], Replica] = {}
        self._active: set[int] = set()
        self._pending_scale: set[int] = set()
        self._failed: set[int] = set()
        self._place_replicas()
        if not self._active:
            self._active = {r.index for r in self.replicas}

    def _route(self, req: Request, rr: dict[str, int], rng: random.Random) -> Replica:
        """Choose a deployment-local replica according to the configured policy."""
        group = [
            r for r in self._by_deployment[req.deployment]
            if r.index in self._active and r.index not in self._failed
        ]
        if not group:
            raise RuntimeError(f"no active replicas for deployment {req.deployment}")
        policy = self.scenario.router.policy
        if policy in ("round_robin", "class_to_pool"):
            replica = group[rr[req.deployment] % len(group)]
            rr[req.deployment] += 1
            return replica
        if policy == "random":
            return rng.choice(group)
        if policy == "session_affinity" and req.session_id is not None:
            key = (req.deployment, req.session_id)
            return self._session_replicas.setdefault(key, group[rr[req.deployment] % len(group)])
        if policy == "least_outstanding":
            def outstanding(r: Replica) -> tuple[int, int, int]:
                queued = len(r.running) + len(r.waiting) + len(r.swapped)
                # Equal request counts should not make a 4k-token prefill as
                # attractive as a nearly completed short request.
                remaining = sum(req.prompt_len + req.output_len - req.num_computed for req in r.running)
                remaining += sum(req.prompt_len + req.output_len for _, req in r.waiting)
                return (queued, remaining, r.index)
            return min(group, key=outstanding)
        if policy == "kv_aware":
            cfg = self.scenario.router
            def score(r: Replica) -> float:
                cached = r.kv.cached_prefix_tokens(req.segments(), req.prompt_len) if hasattr(r.kv, "cached_prefix_tokens") else 0
                load = len(r.running) + len(r.waiting) + len(r.swapped)
                return cfg.kv_overlap_weight * cached - cfg.load_penalty_weight * load
            scores = [score(r) for r in group]
            if len(set(scores)) == 1:
                replica = group[rr[req.deployment] % len(group)]
                rr[req.deployment] += 1
                return replica
            return max(group, key=lambda r: (score(r), -r.index))
        # A session-less affinity request falls back to stable round robin.
        replica = group[rr[req.deployment] % len(group)]
        rr[req.deployment] += 1
        return replica

    def _place_replicas(self) -> None:
        s = self.scenario
        for dep, node in place_replicas(s, max_for_autoscale=s.autoscaler.enabled):
            gpu = get_gpu(s.pool(dep.pool).gpu)
            r = Replica(len(self.replicas), dep, get_model(dep.model), gpu, s.hardware.perf, s.admission, node)
            self.replicas.append(r)
            self._by_deployment.setdefault(dep.name, []).append(r)

        if s.autoscaler.enabled:
            keep = s.autoscaler.min_replicas + s.autoscaler.warm_pool
            self._active = {
                replica.index
                for group in self._by_deployment.values()
                for replica in group[:keep]
            }

    def run(self) -> SimResult:
        s = self.scenario
        run = s.run
        w0, w1 = run.warmup_s, run.duration_s
        horizon = run.duration_s + run.max_drain_s if run.drain else run.duration_s
        stats = [ReplicaStats() for _ in self.replicas]
        rr = {name: 0 for name in self._by_deployment}
        router_rng = random.Random(f"{self.seed}:router")
        records: list[Request] = []
        live: dict[int, Request] = {}
        itl = LogHistogram()
        steps: dict[int, Step] = {}
        heap: list = []
        seq = itertools.count()
        checks = 0

        source = iter(self._requests if self._requests is not None else WorkloadGenerator(s, self.seed))
        retries_used = 0

        last_arrival = [0.0]

        def push_next_arrival() -> None:
            req = next(source, None)
            if req is not None:
                if req.arrival < last_arrival[0]:
                    raise ValueError("requests must be given in arrival order")
                last_arrival[0] = req.arrival
                heapq.heappush(heap, (req.arrival, _ARRIVAL, next(seq), req))

        def start_step(i: int, now: float) -> None:
            nonlocal checks
            replica = self.replicas[i]
            step = replica.schedule(now)
            if self.check:
                replica.kv.check_invariants()
                if step is not None:
                    for req, n, _ in step.items:
                        assert req.status == "running" and n > 0, f"bad step item {req.rid} n={n}"
                checks += 1
            st = stats[i]
            if now > st.last_end:  # idle gap: nothing held
                st.kv_util.add(0.0, overlap(st.last_end, now, w0, w1))
            if step is None:
                replica.busy = False
                st.last_end = now
                if replica.has_work():
                    raise RuntimeError(f"replica {i} stalled with queued work")  # a scheduler bug
                return
            end = now + step.duration
            in_window = overlap(now, end, w0, w1)
            kv = replica.kv
            kstats = kv.stats()
            st.kv_util.add(kstats.used / kstats.total, in_window)
            if kstats.used:
                st.waste_ratio.add(kv.internal_waste_tokens() / (kstats.used * kv.block_size), in_window)
            st.ext_frag.add(kv.external_fragmentation(), in_window)
            if in_window:
                c = step.cost
                frac = in_window / step.duration
                perf = replica.perf.params
                st.bound_s += max(c.compute_s, c.memory_s) * frac
                st.flops_s += c.compute_s * perf.mfu * frac
                st.hbm_s += c.memory_s * perf.hbm_efficiency * frac
            st.last_end = end
            replica.busy = True
            steps[i] = step
            heapq.heappush(heap, (end, _STEP_DONE, next(seq), i))

        def admit(req: Request) -> None:
            """Route and admit an initial arrival or a pre-first-token retry."""
            replica = self._route(req, rr, router_rng)
            outcome = replica.admit(req)
            if outcome is None:
                if not replica.busy:
                    replica.busy = True
                    heapq.heappush(heap, (now, _KICK, next(seq), replica.index))
                return
            req.status, _, reason = outcome.partition(":")
            req.reason = reason or None
            req.finish = now
            records.append(req)

        def failure_targets(event) -> list[int]:
            if event.scope == "replica":
                return [event.target]
            if event.scope == "node":
                return [r.index for r in self.replicas if r.node == event.target]
            if event.scope == "zone":
                return [
                    r.index for r in self.replicas
                    if s.pool(r.dep.pool).zone == event.target
                ]
            available = [r.index for r in self.replicas if r.index not in self._failed]
            count = max(1, round(len(available) * event.fraction))
            return available[:count]

        n_in_system = 0
        last_t = 0.0
        integral_full = 0.0
        integral_window = 0.0
        now = 0.0
        push_next_arrival()
        if s.autoscaler.enabled:
            heapq.heappush(heap, (0.0, _AUTOSCALE, next(seq), None))
        if s.failures.enabled:
            for event in s.failures.events:
                heapq.heappush(heap, (event.at_s, _FAILURE, next(seq), event))
        while heap:
            t = heap[0][0]
            if t > horizon:
                break
            _, kind, _, obj = heapq.heappop(heap)
            integral_full += n_in_system * (t - last_t)
            integral_window += n_in_system * overlap(last_t, t, w0, w1)
            last_t = now = t
            if kind == _ARRIVAL:
                req: Request = obj
                push_next_arrival()
                live[req.rid] = req
                n_in_system += 1
                admit(req)
                if req.status in ("failed", "shed"):
                    del live[req.rid]
                    n_in_system -= 1
            elif kind == _KICK:
                if obj not in self._failed:
                    start_step(obj, now)
            elif kind == _AUTOSCALE:
                active = [r for r in self.replicas if r.index in self._active]
                queued = sum(len(r.waiting) + len(r.running) for r in active)
                cfg = s.autoscaler
                limit = cfg.max_replicas or len(self.replicas)
                if queued > cfg.target and len(active) + len(self._pending_scale) < limit:
                    candidate = next((r for r in self.replicas if r.index not in self._active and r.index not in self._pending_scale), None)
                    if candidate is not None:
                        self._pending_scale.add(candidate.index)
                        delay = cfg.warm_start_s if len(active) < cfg.min_replicas + cfg.warm_pool else cfg.cold_start_s
                        heapq.heappush(heap, (now + delay, _SCALE_READY, next(seq), candidate.index))
                if now + cfg.interval_s <= horizon:
                    heapq.heappush(heap, (now + cfg.interval_s, _AUTOSCALE, next(seq), None))
            elif kind == _SCALE_READY:
                self._pending_scale.discard(obj)
                if obj not in self._failed:
                    self._active.add(obj)
            elif kind == _FAILURE:
                targets = failure_targets(obj)
                for i in targets:
                    if i in self._failed:
                        continue
                    self._failed.add(i)
                    self._pending_scale.discard(i)
                    replica = self.replicas[i]
                    steps.pop(i, None)  # a stale step-done event becomes a no-op
                    for req in replica.fail_all():
                        can_retry = (
                            req.first_token is None
                            and req.retries < s.failures.retry_max_attempts
                            and retries_used < max(1, round((len(live) + len(records)) * s.failures.retry_budget_fraction))
                        )
                        if can_retry:
                            retries_used += 1
                            req.retries += 1
                            req.status = "pending"
                            req.reason = None
                            req.num_computed = req.generated = 0
                            req.first_scheduled = req.first_token = req.last_token = None
                            heapq.heappush(heap, (now + s.failures.retry_backoff_s, _RETRY, next(seq), req))
                        else:
                            req.status = "failed"
                            req.reason = "replica_failure"
                            req.finish = now
                            live.pop(req.rid, None)
                            n_in_system -= 1
                            records.append(req)
                if obj.recover_after_s is not None:
                    heapq.heappush(heap, (now + obj.recover_after_s, _RECOVER, next(seq), targets))
            elif kind == _RECOVER:
                self._failed.difference_update(obj)
            elif kind == _RETRY:
                if req := live.get(obj.rid):
                    try:
                        admit(req)
                    except RuntimeError:
                        req.status = "failed"
                        req.reason = "no_healthy_replica"
                        req.finish = now
                        live.pop(req.rid, None)
                        n_in_system -= 1
                        records.append(req)
            else:
                i: int = obj
                if i in self._failed or i not in steps:
                    continue
                replica = self.replicas[i]
                tokens, finished = replica.finish_step(steps.pop(i), now)
                in_win = w0 <= now <= w1
                for req, gap in tokens:
                    if in_win:
                        stats[i].output_tokens += 1
                    if gap is not None and w0 <= req.arrival < w1:
                        itl.add(gap)
                for req in finished:
                    del live[req.rid]
                    n_in_system -= 1
                    records.append(req)
                start_step(i, now)

        # Stopped early (horizon reached): the run ends at the horizon. Otherwise at
        # the last event, but never before the arrival window closes (idle tail).
        end_time = horizon if heap else max(now, run.duration_s)
        integral_full += n_in_system * (end_time - last_t)
        integral_window += n_in_system * overlap(last_t, end_time, w0, w1)
        for st in stats:
            if end_time > st.last_end:
                st.kv_util.add(0.0, overlap(st.last_end, end_time, w0, w1))
        for req in sorted(live.values(), key=lambda r: r.rid):
            req.status = "unfinished"
            records.append(req)
        records.sort(key=lambda r: r.rid)
        return SimResult(
            scenario=s,
            seed=self.seed,
            replicas=self.replicas,
            stats=stats,
            records=records,
            window=(w0, w1),
            end_time=end_time,
            itl=itl,
            in_system_integral_full=integral_full,
            in_system_integral_window=integral_window,
            invariant_checks=checks,
        )

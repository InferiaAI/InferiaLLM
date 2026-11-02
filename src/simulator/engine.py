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
        self._place_replicas()

    def _route(self, req: Request, rr: dict[str, int], rng: random.Random) -> Replica:
        """Choose a deployment-local replica according to the configured policy."""
        group = self._by_deployment[req.deployment]
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
        for dep, node in place_replicas(s):
            gpu = get_gpu(s.pool(dep.pool).gpu)
            r = Replica(len(self.replicas), dep, get_model(dep.model), gpu, s.hardware.perf, s.admission, node)
            self.replicas.append(r)
            self._by_deployment.setdefault(dep.name, []).append(r)

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

        n_in_system = 0
        last_t = 0.0
        integral_full = 0.0
        integral_window = 0.0
        now = 0.0
        push_next_arrival()
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
                replica = self._route(req, rr, router_rng)
                outcome = replica.admit(req)
                if outcome is None:
                    live[req.rid] = req
                    n_in_system += 1
                    if not replica.busy:
                        replica.busy = True  # a step is pending; no second kick
                        heapq.heappush(heap, (now, _KICK, next(seq), replica.index))
                else:
                    req.status, _, reason = outcome.partition(":")
                    req.reason = reason or None
                    req.finish = now
                    records.append(req)
            elif kind == _KICK:
                start_step(obj, now)
            else:
                i: int = obj
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

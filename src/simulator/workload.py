"""Requests and the synthetic workload generator.

This slice generates Poisson arrivals at a fixed rate, picks a workload class
by weight, and samples prompt and output lengths per class. A class can put
one of N shared prefixes (for example system prompts) in front of each
prompt, which is what makes prefix caching visible. Users, sessions,
diurnal curves, bursts and trace replay come with the workload slice (T3).

Token identity is carried by segment ids (see ``simulator.kv``). Ids are
non-negative and disjoint by construction: ``3*rid`` for a request's own
prompt tokens, ``3*rid + 1`` for its output, ``3*k + 2`` for shared prefix k.
"""

from __future__ import annotations

import math
import random
import json
from dataclasses import dataclass, field
from typing import Iterator, Optional

from simulator.classes import PRIORITY, WorkloadClass
from simulator.config import ClassMix, FixedDist, Scenario, TokenDist

# Shared-prefix ids of different classes must not collide.
_PREFIX_GROUPS_PER_CLASS = 1_000_003


@dataclass(eq=False)
class Request:
    rid: int
    arrival: float
    workload_class: WorkloadClass
    prompt_segments: list[tuple[int, int]]
    output_len: int
    deployment: str
    output_segment: Optional[int] = None
    # Runtime state, owned by the replica scheduler.
    replica: int = -1
    status: str = "pending"  # pending|waiting|running|swapped|completed|failed|shed|unfinished
    reason: Optional[str] = None
    num_computed: int = 0
    generated: int = 0
    first_scheduled: Optional[float] = None
    first_token: Optional[float] = None
    last_token: Optional[float] = None
    finish: Optional[float] = None
    preemptions: int = 0
    session_id: Optional[str] = None
    turn_index: int = 0
    retries: int = 0
    kv_transferred: bool = False
    prompt_len: int = field(init=False)
    priority: int = field(init=False)

    def __post_init__(self) -> None:
        if self.output_len <= 0:
            raise ValueError("output_len must be positive")
        self.prompt_len = sum(n for _, n in self.prompt_segments)
        if self.prompt_len <= 0:
            raise ValueError("a prompt needs at least one token")
        if self.output_segment is None:
            self.output_segment = 3 * self.rid + 1
        self.priority = PRIORITY[self.workload_class]

    @property
    def num_tokens(self) -> int:
        """Tokens whose KV must exist before the next token is sampled."""
        return self.prompt_len + self.generated

    def segments(self) -> list[tuple[int, int]]:
        """The full token sequence: prompt then output."""
        return list(self.prompt_segments) + [(self.output_segment, self.output_len)]

    @property
    def queue(self) -> Optional[float]:
        return None if self.first_scheduled is None else self.first_scheduled - self.arrival

    @property
    def ttft(self) -> Optional[float]:
        return None if self.first_token is None else self.first_token - self.arrival

    @property
    def e2e(self) -> Optional[float]:
        return None if self.finish is None else self.finish - self.arrival


def sample_tokens(dist: TokenDist, rng: random.Random) -> int:
    if isinstance(dist, FixedDist):
        return dist.value
    value = round(rng.lognormvariate(math.log(dist.median), dist.sigma))
    return min(max(value, dist.min), dist.max)


def describe(dist: TokenDist) -> str:
    if isinstance(dist, FixedDist):
        return f"fixed {dist.value}"
    return f"lognormal median={dist.median} sigma={dist.sigma} clip=[{dist.min},{dist.max}]"


class WorkloadGenerator:
    """Yields requests in arrival order until ``run.duration_s``.

    Separate random streams for arrivals, classes, lengths and prefixes keep a
    change to one (say, a new length distribution) from reshuffling the others.
    """

    def __init__(self, scenario: Scenario, seed: int):
        self.scenario = scenario
        self._arrival = random.Random(f"{seed}:arrival")
        self._class = random.Random(f"{seed}:class")
        self._tokens = random.Random(f"{seed}:tokens")
        self._prefix = random.Random(f"{seed}:prefix")
        self._mixes: list[ClassMix] = list(scenario.workload.classes)
        self._weights = [m.weight for m in self._mixes]
        self._deployments = [scenario.deployment_for(m).name for m in self._mixes]

    def __iter__(self) -> Iterator[Request]:
        arrival = self.scenario.workload.arrival
        if arrival.process == "trace":
            assert arrival.trace_path is not None  # validated by config
            with open(arrival.trace_path, encoding="utf-8") as f:
                for rid, line in enumerate(f):
                    item = json.loads(line)
                    t = float(item["arrival_s"])
                    if t >= self.scenario.run.duration_s:
                        continue
                    mix = self._mixes[0]
                    yield Request(rid, t, mix.workload_class,
                                  [(3 * rid, int(item["input_tokens"]))],
                                  int(item["output_tokens"]), self._deployments[0],
                                  session_id=item.get("session_id"))
            return

        rate = self.scenario.workload.effective_rate_rps()
        end = self.scenario.run.duration_s
        t = 0.0
        rid = 0
        indices = range(len(self._mixes))
        generated: list[Request] = []
        while True:
            # Thinning gives an exact NHPP for the configured sinusoidal
            # diurnal curve; MMPP's explicit burst window is a deterministic
            # two-state special case used for scenario what-ifs.
            local_rate = rate
            if arrival.process == "nhpp" and arrival.diurnal is not None:
                d = arrival.diurnal
                local_rate *= 1 + (d.peak_multiplier - 1) * (1 + math.sin(2 * math.pi * t / d.period_s - math.pi / 2)) / 2
            if arrival.process == "mmpp" and arrival.burst is not None:
                b = arrival.burst
                if b.start_s is not None and b.duration_s is not None and b.start_s <= t < b.start_s + b.duration_s:
                    local_rate *= b.multiplier
            # A lightweight self-exciting approximation: each arrival raises
            # the next rate by alpha.  It remains deterministic per seed.
            if arrival.process == "hawkes" and arrival.hawkes is not None:
                local_rate += arrival.hawkes.alpha * rate
            t += self._arrival.expovariate(local_rate)
            if t >= end:
                break
            k = self._class.choices(indices, weights=self._weights)[0]
            mix = self._mixes[k]
            prompt = [(3 * rid, sample_tokens(mix.input_tokens, self._tokens))]
            out = sample_tokens(mix.output_tokens, self._tokens)
            if mix.shared_prefix is not None:
                group = self._prefix.randrange(mix.shared_prefix.groups)
                pid = 3 * (k * _PREFIX_GROUPS_PER_CLASS + group) + 2
                prompt.insert(0, (pid, mix.shared_prefix.tokens))
            first = Request(
                rid=rid,
                arrival=t,
                workload_class=mix.workload_class,
                prompt_segments=prompt,
                output_len=out,
                deployment=self._deployments[k],
            )
            if mix.sessions is None:
                generated.append(first)
            else:
                sid = f"s-{rid}"
                prefix = list(prompt)
                for turn in range(mix.sessions.turns):
                    turn_rid = rid + turn
                    if turn:
                        prefix.append((3 * (turn_rid - 1) + 1, out))
                        prefix.append((3 * turn_rid, sample_tokens(mix.input_tokens, self._tokens)))
                        out = sample_tokens(mix.output_tokens, self._tokens)
                    generated.append(Request(
                        rid=turn_rid, arrival=t + turn * mix.sessions.think_time_s,
                        workload_class=mix.workload_class, prompt_segments=list(prefix), output_len=out,
                        deployment=self._deployments[k], session_id=sid, turn_index=turn,
                    ))
                rid += mix.sessions.turns - 1
            rid += 1
        yield from sorted(generated, key=lambda r: (r.arrival, r.rid))

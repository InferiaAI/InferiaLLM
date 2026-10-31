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
        rate = self.scenario.workload.arrival.rate_rps
        end = self.scenario.run.duration_s
        t = 0.0
        rid = 0
        indices = range(len(self._mixes))
        while True:
            t += self._arrival.expovariate(rate)
            if t >= end:
                return
            k = self._class.choices(indices, weights=self._weights)[0]
            mix = self._mixes[k]
            prompt = [(3 * rid, sample_tokens(mix.input_tokens, self._tokens))]
            out = sample_tokens(mix.output_tokens, self._tokens)
            if mix.shared_prefix is not None:
                group = self._prefix.randrange(mix.shared_prefix.groups)
                pid = 3 * (k * _PREFIX_GROUPS_PER_CLASS + group) + 2
                prompt.insert(0, (pid, mix.shared_prefix.tokens))
            yield Request(
                rid=rid,
                arrival=t,
                workload_class=mix.workload_class,
                prompt_segments=prompt,
                output_len=out,
                deployment=self._deployments[k],
            )
            rid += 1

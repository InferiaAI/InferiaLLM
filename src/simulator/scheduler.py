"""One model replica: a vLLM-V1-like continuous-batching scheduler over a KV manager.

Each call to ``schedule`` builds one engine iteration (forward pass):

1. Running requests first, in the order they started. Each gets its
   remaining tokens (prefill chunk, or 1 for decode), capped by the step's
   token budget (``max_num_batched_tokens``) when chunked prefill is on. If
   the KV manager cannot grow a request, a victim is preempted (lowest
   workload-class priority, then latest arrival) until it can, or until the
   request preempts itself. This mirrors vLLM V1's scheduler, which preempts
   from the running queue on allocation failure
   (https://docs.vllm.ai/en/latest/configuration/optimization.html: decode
   requests are batched before prefills; default preemption is recompute).
   Decoding requests go before running prefills ("batches all pending decode
   requests before scheduling any prefill operations", same page).
2. Only if nothing was preempted this step: swapped-out requests are brought
   back (and compute in this step), then waiting requests are admitted in priority order (class
   priority, then arrival) while ``max_num_seqs`` and the token budget allow.
   A waiting request reuses its longest cached prefix. Head-of-line blocking
   is kept on purpose: if the head cannot be allocated, admission stops.

Preemption:
- ``recompute`` frees the victim's blocks; it rejoins the waiting queue and
  later recomputes prompt + generated tokens (prefix hits still apply).
- ``swap`` copies its blocks to the CPU pool (PCIe time is charged to the
  step) and swaps them back later. If CPU space is short it falls back to
  recompute instead of failing the request.

Admission (on arrival): prompt + output above ``max_model_len`` is rejected
(``context_length_exceeded``, the 400 a real server returns); a full waiting
queue sheds the request (429 with Retry-After). Config validation already
guarantees the KV cache holds ``max_model_len`` tokens, so an admitted
request always fits once the replica drains.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Optional

from simulator.config import AdmissionConfig, DeploymentConfig, PerfParams, kv_blocks_per_replica
from simulator.hardware import GB, GpuSpec, ModelSpec
from simulator.kv import ContiguousAllocator, PagedBlockManager
from simulator.perf import PerfModel, StepCost
from simulator.workload import Request


@dataclass
class Step:
    start: float
    duration: float
    items: list[tuple[Request, int, int]]  # (request, new tokens, cached context before)
    cost: StepCost


def _queue_key(req: Request) -> tuple[int, float, int]:
    return (-req.priority, req.arrival, req.rid)


def _victim_key(req: Request) -> tuple[int, float, int]:
    # max() of this picks the lowest priority, then the latest arrival.
    return (-req.priority, req.arrival, req.rid)


class Replica:
    def __init__(
        self,
        index: int,
        deployment: DeploymentConfig,
        model: ModelSpec,
        gpu: GpuSpec,
        perf: PerfParams,
        admission: AdmissionConfig,
        node: str,
    ):
        self.index = index
        self.node = node
        self.dep = deployment
        self.gpu = gpu
        self.admission = admission
        self.perf = PerfModel(model, gpu, deployment.tensor_parallel, perf)
        self.block_bytes = deployment.block_size * model.kv_bytes_per_token()
        blocks = kv_blocks_per_replica(deployment, gpu, perf)
        cpu_blocks = 0
        if deployment.preemption == "swap":
            cpu_blocks = int(deployment.swap_space_gb * GB * deployment.tensor_parallel // self.block_bytes)
        if deployment.allocator == "paged":
            offload_blocks = 0
            if deployment.offload is not None:
                offload_blocks = int(
                    deployment.offload.capacity_gb * GB * deployment.tensor_parallel // self.block_bytes
                )
            self.kv = PagedBlockManager(
                blocks, deployment.block_size, deployment.enable_prefix_caching, cpu_blocks,
                eviction=deployment.eviction, offload_num_blocks=offload_blocks,
            )
        else:
            self.kv = ContiguousAllocator(blocks, deployment.block_size, cpu_blocks)
        self.waiting: list[tuple[tuple[int, float, int], Request]] = []
        self.swapped: list[tuple[tuple[int, float, int], Request]] = []
        self.running: list[Request] = []
        self.busy = False
        # Counters.
        self.preemptions = 0
        self.query_tokens = 0
        self.hit_tokens = 0
        self.steps = 0

    # -- arrivals --------------------------------------------------------------------

    def admit(self, req: Request) -> Optional[str]:
        """Queue the request, or return why not: 'failed:<reason>' or 'shed'."""
        req.replica = self.index
        if req.prompt_len + req.output_len > self.dep.max_model_len:
            return "failed:context_length_exceeded"
        if len(self.waiting) >= self.admission.max_waiting_per_replica:
            return "shed"
        reserve = (
            self.dep.max_model_len
            if self.dep.contiguous_reserve == "max_model_len"
            else req.prompt_len + req.output_len
        )
        self.kv.add_request(req.rid, req.segments(), reserve_tokens=reserve)
        req.status = "waiting"
        heapq.heappush(self.waiting, (_queue_key(req), req))
        return None

    def has_work(self) -> bool:
        return bool(self.running or self.waiting or self.swapped)

    # -- one iteration -----------------------------------------------------------------

    def schedule(self, now: float) -> Optional[Step]:
        budget = self.dep.max_num_batched_tokens
        chunked = self.dep.enable_chunked_prefill
        scheduled: dict[int, tuple[Request, int, int]] = {}
        preempted = False
        swap_bytes = 0.0
        offload_bytes = 0.0

        # Decodes first, then running prefills (vLLM: "batches all pending decode
        # requests before scheduling any prefill"); each group keeps start order.
        decodes = [r for r in self.running if r.num_tokens - r.num_computed == 1]
        prefills = [r for r in self.running if r.num_tokens - r.num_computed > 1]
        for req in decodes + prefills:
            if req.status != "running":
                continue  # preempted earlier in this loop
            # Without chunked prefill a running request always has exactly one token
            # left (its whole prompt ran in its first step), so min() only bites
            # when chunking. Validation keeps budget >= max_num_seqs, so decodes
            # never exhaust it; the check guards against scheduling 0 tokens.
            n = min(req.num_tokens - req.num_computed, budget)
            if n <= 0:
                break
            while not self.kv.allocate(req.rid, req.num_computed + n):
                victim = max(self.running, key=_victim_key)
                if victim.rid in scheduled:
                    budget += scheduled.pop(victim.rid)[1]
                swap_bytes += self._preempt(victim)
                preempted = True
                if victim is req:
                    break
            else:
                scheduled[req.rid] = (req, n, req.num_computed)
                budget -= n

        if not preempted:
            # Swapped-out requests come back before new ones and compute this step.
            while self.swapped and budget > 0 and len(self.running) < self.dep.max_num_seqs:
                req = self.swapped[0][1]
                n = min(req.num_tokens - req.num_computed, budget)  # as above
                moved = self.kv.swapped_blocks(req.rid)
                if not self.kv.swap_in(req.rid, req.num_computed + n):
                    break
                heapq.heappop(self.swapped)
                swap_bytes += moved * self.block_bytes
                req.status = "running"
                self.running.append(req)
                scheduled[req.rid] = (req, n, req.num_computed)
                budget -= n
            while self.waiting and budget > 0 and len(self.running) < self.dep.max_num_seqs:
                req = self.waiting[0][1]
                hit = self.kv.lookup(req.rid, req.num_tokens)
                n = req.num_tokens - hit
                if chunked:
                    n = min(n, budget)
                elif n > budget:
                    break
                if not self.kv.allocate(req.rid, hit + n, hit_tokens=hit):
                    break
                if hasattr(self.kv, "consume_offload_reload_blocks"):
                    reloads = self.kv.consume_offload_reload_blocks()
                    if reloads and self.dep.offload is not None:
                        # Gbit/s -> bytes/s.  The fixed latency is paid once
                        # per restore batch, like a DMA submission.
                        offload_bytes += reloads * self.block_bytes
                heapq.heappop(self.waiting)
                self.query_tokens += req.num_tokens
                self.hit_tokens += hit
                req.num_computed = hit
                if req.first_scheduled is None:
                    req.first_scheduled = now
                req.status = "running"
                self.running.append(req)
                scheduled[req.rid] = (req, n, hit)
                budget -= n

        if not scheduled:
            # A step always computes something: a request that is alone always fits
            # (validation guarantees max_model_len of KV), so preemption leaves at
            # least the preemptor scheduled.
            return None
        items = list(scheduled.values())
        cost = self.perf.step_time([(n, c) for _, n, c in items])
        duration = cost.total_s + self.perf.transfer_time(swap_bytes)
        if offload_bytes and self.dep.offload is not None:
            duration += offload_bytes / (self.dep.offload.bandwidth_gbps * 1e9)
        if self.dep.disaggregation is not None:
            transfer = self.dep.disaggregation
            for req, n, _ in items:
                # The prompt step emits token one (and establishes TTFT) on
                # the prefill side. Before its first decode iteration, move
                # prompt KV once to the decode side.
                if req.generated == 1 and n == 1 and not req.kv_transferred:
                    duration += transfer.transfer_latency_s
                    duration += req.prompt_len * (self.block_bytes / self.dep.block_size) / (transfer.transfer_bandwidth_gbps * 1e9)
                    req.kv_transferred = True
        self.steps += 1
        return Step(now, duration, items, cost)

    def _preempt(self, victim: Request) -> float:
        """Preempt a running request. Returns bytes copied to the CPU (swap)."""
        self.running.remove(victim)
        victim.preemptions += 1
        self.preemptions += 1
        if self.dep.preemption == "swap" and self.kv.can_swap_out(victim.rid):
            blocks = self.kv.swap_out(victim.rid)
            victim.status = "swapped"
            heapq.heappush(self.swapped, (_queue_key(victim), victim))
            return blocks * self.block_bytes
        self.kv.release(victim.rid)
        victim.num_computed = 0
        victim.status = "waiting"
        heapq.heappush(self.waiting, (_queue_key(victim), victim))
        return 0.0

    def finish_step(self, step: Step, now: float) -> tuple[list[tuple[Request, Optional[float]]], list[Request]]:
        """Apply a finished iteration.

        Returns (tokens, finished): one (request, inter-token gap or None for
        the first token) per sampled token, and the requests that completed.
        """
        tokens: list[tuple[Request, Optional[float]]] = []
        finished: list[Request] = []
        for req, n, _ in step.items:
            req.num_computed += n
            self.kv.commit(req.rid, req.num_computed)
            if req.num_computed < req.num_tokens:
                continue  # a prefill chunk that did not reach the end of the prompt
            req.generated += 1
            gap = None if req.last_token is None else now - req.last_token
            if req.first_token is None:
                req.first_token = now
            req.last_token = now
            tokens.append((req, gap))
            if req.generated == req.output_len:
                req.finish = now
                req.status = "completed"
                self.running.remove(req)
                self.kv.remove(req.rid)
                finished.append(req)
        return tokens, finished

"""KV-cache block managers.

``PagedBlockManager`` models vLLM's paged KV cache with automatic prefix
caching (APC), following the design doc
https://docs.vllm.ai/en/latest/design/prefix_caching.html (read 2026-10-31):

- The cache is a pool of fixed-size blocks (vLLM's default block size is 16
  tokens). A request holds a list of blocks; blocks are allocated on demand
  as its token count grows, so only the last block is partially filled
  (internal fragmentation is under one block per request).
- Only FULL blocks are cached. A block's key chains its parent's key with
  the identity of the tokens it holds, so two requests share a block exactly
  when they share every token up to the end of that block.
- Each block has a reference count. Blocks with ``ref == 0`` sit in a free
  queue ordered least-recently-freed first. A zero-ref block that still
  carries a key stays in the prefix cache and can be hit; it is evicted
  (its key dropped) only when the allocator pops it from the queue head.
- A finished request's blocks are freed in REVERSE order, so a request's
  tail blocks (which hash more tokens and are less likely to be reused) are
  evicted before its head blocks.
- When every prompt token would hit, the last one is still recomputed (the
  engine needs at least one token to compute to produce logits).

Tokens are not simulated individually. A request's token sequence is a list
of segments ``(segment_id, length)``; equal (non-negative) segment ids mean
equal tokens. A block key is Python's 64-bit tuple hash of (parent key,
pieces), the same idea as vLLM's default builtin-hash mode. Keys could in
principle collide; at 6e7 distinct blocks the probability is ~1e-4 per run,
which we accept (vLLM offers sha256 for adversarial settings; a simulator
has none).

``ContiguousAllocator`` models pre-PagedAttention serving systems, which
reserve one contiguous region per request for its maximum length up front.
It exists to show the internal and external fragmentation that paging
avoids (PagedAttention paper, Kwon et al., SOSP 2023,
https://arxiv.org/abs/2309.06180).

Both managers support swapping a request's blocks to a CPU pool, which is
how vLLM V0's ``swap`` preemption worked. vLLM V1 only preempts by
recompute (https://docs.vllm.ai/en/latest/configuration/optimization.html);
the swap path is kept to model V0 and CPU-offload designs.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional, Sequence

Segments = Sequence[tuple[int, int]]


@dataclass(frozen=True)
class KVStats:
    total: int
    used: int  # ref > 0
    cached_free: int  # ref == 0, still in the prefix cache (evictable)
    free: int  # ref == 0, no cached content


class _Layout:
    """Lazily computed block keys of one request's token sequence."""

    __slots__ = ("segments", "keys", "_seg", "_off")

    def __init__(self, segments: Segments):
        if any(sid < 0 or n < 0 for sid, n in segments):
            # Negative ids are refused because CPython hashes -1 and -2 alike.
            raise ValueError("segment ids and lengths must be non-negative")
        self.segments = [(sid, n) for sid, n in segments if n > 0]
        self.keys: list[int] = []
        self._seg = 0  # cursor: segment index and offset of the next block start
        self._off = 0

    def key(self, i: int, block_size: int) -> Optional[int]:
        """Key of full block ``i``, or None if the sequence ends before it is full."""
        while len(self.keys) <= i:
            pieces = []
            need = block_size
            seg, off = self._seg, self._off
            while need and seg < len(self.segments):
                sid, n = self.segments[seg]
                take = min(need, n - off)
                pieces.append((sid, off, off + take))
                need -= take
                off += take
                if off == n:
                    seg, off = seg + 1, 0
            if need:
                return None
            parent = self.keys[-1] if self.keys else 0
            self.keys.append(hash((parent, tuple(pieces))))
            self._seg, self._off = seg, off
        return self.keys[i]


class _Base:
    def __init__(self, num_blocks: int, block_size: int, cpu_num_blocks: int = 0):
        if num_blocks <= 0 or block_size <= 0 or cpu_num_blocks < 0:
            raise ValueError("num_blocks and block_size must be positive")
        self.num_blocks = num_blocks
        self.block_size = block_size
        self.cpu_num_blocks = cpu_num_blocks
        self.cpu_used = 0
        self._swapped: dict[int, int] = {}  # rid -> blocks held on the CPU
        self._tokens: dict[int, int] = {}  # rid -> tokens allocated for
        self.swapped_out_blocks = 0
        self.evictions = 0

    def _blocks_for(self, tokens: int) -> int:
        return -(-tokens // self.block_size)

    def swapped_blocks(self, rid: int) -> int:
        return self._swapped.get(rid, 0)

    def can_swap_out(self, rid: int) -> bool:
        return self.num_blocks_of(rid) <= self.cpu_num_blocks - self.cpu_used

    def swap_out(self, rid: int) -> int:
        """Copy the request's blocks to the CPU pool and free them on the GPU."""
        if not self.can_swap_out(rid):
            raise RuntimeError("not enough CPU swap space")
        n = self.num_blocks_of(rid)
        self.release(rid)
        self._swapped[rid] = n
        self.cpu_used += n
        self.swapped_out_blocks += n
        return n

    def swap_in(self, rid: int, num_tokens: int) -> bool:
        """Bring a swapped-out request back. False (no change) if the GPU lacks room."""
        n = self._swapped.pop(rid)
        if not self.allocate(rid, num_tokens):
            self._swapped[rid] = n
            return False
        self.cpu_used -= n
        return True

    def _check_allocatable(self, rid: int) -> None:
        if rid in self._swapped:
            raise RuntimeError(f"request {rid} is swapped out; swap it in first")


class PagedBlockManager(_Base):
    def __init__(
        self,
        num_blocks: int,
        block_size: int,
        enable_prefix_caching: bool = True,
        cpu_num_blocks: int = 0,
    ):
        super().__init__(num_blocks, block_size, cpu_num_blocks)
        self.enable_prefix_caching = enable_prefix_caching
        self._ref = [0] * num_blocks
        self._key: list[Optional[int]] = [None] * num_blocks
        self._cache: dict[int, int] = {}  # key -> block
        self._free_q: OrderedDict[int, None] = OrderedDict((b, None) for b in range(num_blocks))
        self._layouts: dict[int, _Layout] = {}
        self._blocks: dict[int, list[int]] = {}
        self._hashed: dict[int, int] = {}  # rid -> blocks already offered to the cache
        self._num_cached_free = 0

    # -- requests -------------------------------------------------------------------

    def add_request(self, rid: int, segments: Segments, reserve_tokens: Optional[int] = None) -> None:
        self._layouts[rid] = _Layout(segments)

    def blocks_of(self, rid: int) -> list[int]:
        return list(self._blocks.get(rid, ()))

    def num_blocks_of(self, rid: int) -> int:
        return len(self._blocks.get(rid, ()))

    def lookup(self, rid: int, num_tokens: int) -> int:
        """Tokens of the first ``num_tokens`` already in the prefix cache.

        A multiple of the block size, and always < ``num_tokens``.
        """
        if not self.enable_prefix_caching:
            return 0
        layout = self._layouts[rid]
        hits = 0
        for i in range((num_tokens - 1) // self.block_size):
            key = layout.key(i, self.block_size)
            if key is None or key not in self._cache:
                break
            hits += 1
        return hits * self.block_size

    def allocate(self, rid: int, num_tokens: int, hit_tokens: int = 0) -> bool:
        """Make the request hold enough blocks for ``num_tokens`` tokens.

        ``hit_tokens`` (from ``lookup``, first allocation only) reuses cached
        blocks for the prefix. Returns False and changes nothing if the free
        pool is too small.
        """
        if rid not in self._layouts:
            raise KeyError(rid)
        self._check_allocatable(rid)
        blocks = self._blocks.get(rid)
        hit_blocks: list[int] = []
        if hit_tokens:
            if blocks:
                raise ValueError("prefix hits are only valid on a request's first allocation")
            layout = self._layouts[rid]
            for i in range(hit_tokens // self.block_size):
                hit_blocks.append(self._cache[layout.key(i, self.block_size)])
        have = len(blocks) if blocks else 0
        need = self._blocks_for(num_tokens) - have - len(hit_blocks)
        touched_free = sum(1 for b in hit_blocks if self._ref[b] == 0)
        if need > len(self._free_q) - touched_free:
            return False
        if blocks is None:
            blocks = self._blocks[rid] = []
            self._hashed[rid] = 0
        for b in hit_blocks:
            if self._ref[b] == 0:
                del self._free_q[b]
                self._num_cached_free -= 1
            self._ref[b] += 1
            blocks.append(b)
        self._hashed[rid] = max(self._hashed[rid], len(hit_blocks))
        for _ in range(max(need, 0)):
            b, _ = self._free_q.popitem(last=False)
            key = self._key[b]
            if key is not None:  # evict from the prefix cache
                self._num_cached_free -= 1
                self._key[b] = None
                if self._cache.get(key) == b:
                    del self._cache[key]
                self.evictions += 1
            self._ref[b] = 1
            blocks.append(b)
        self._tokens[rid] = max(num_tokens, self._tokens.get(rid, 0))
        return True

    def commit(self, rid: int, num_computed_tokens: int) -> None:
        """Offer the request's newly computed full blocks to the prefix cache."""
        if not self.enable_prefix_caching or rid not in self._blocks:
            return
        blocks = self._blocks[rid]
        full = min(num_computed_tokens // self.block_size, len(blocks))
        layout = self._layouts[rid]
        for i in range(self._hashed[rid], full):
            key = layout.key(i, self.block_size)
            b = blocks[i]
            # b was allocated fresh (keys are cleared on eviction), so it has no key yet.
            if key is not None and key not in self._cache:
                self._cache[key] = b
                self._key[b] = key
        self._hashed[rid] = max(self._hashed[rid], full)

    def release(self, rid: int) -> None:
        """Free the request's blocks (reverse order) but keep its layout (recompute)."""
        blocks = self._blocks.pop(rid, None)
        self._hashed.pop(rid, None)
        self._tokens.pop(rid, None)
        if not blocks:
            return
        for b in reversed(blocks):
            self._ref[b] -= 1
            if self._ref[b] == 0:
                self._free_q[b] = None
                if self._key[b] is not None:
                    self._num_cached_free += 1

    def remove(self, rid: int) -> None:
        """Forget a finished request entirely."""
        self.release(rid)
        n = self._swapped.pop(rid, 0)
        self.cpu_used -= n
        self._layouts.pop(rid, None)

    # -- accounting -----------------------------------------------------------------

    def stats(self) -> KVStats:
        free_total = len(self._free_q)
        return KVStats(
            total=self.num_blocks,
            used=self.num_blocks - free_total,
            cached_free=self._num_cached_free,
            free=free_total - self._num_cached_free,
        )

    def internal_waste_tokens(self) -> int:
        return sum(len(b) * self.block_size - self._tokens.get(r, 0) for r, b in self._blocks.items())

    def external_fragmentation(self) -> float:
        return 0.0  # any free block can serve any request

    def check_invariants(self) -> None:
        holders = [0] * self.num_blocks
        for blocks in self._blocks.values():
            assert len(set(blocks)) == len(blocks), "a request holds a block twice"
            for b in blocks:
                holders[b] += 1
        for b in range(self.num_blocks):
            assert self._ref[b] >= 0, f"negative ref count on block {b}"
            assert self._ref[b] == holders[b], f"block {b}: ref {self._ref[b]} != holders {holders[b]}"
            assert (self._ref[b] == 0) == (b in self._free_q), f"block {b}: free-queue mismatch"
        for key, b in self._cache.items():
            assert self._key[b] == key, f"cache maps {key} to block {b}, which holds another key"
        assert sum(1 for b in range(self.num_blocks) if self._key[b] is not None) == len(self._cache)
        assert self._num_cached_free == sum(1 for b in self._free_q if self._key[b] is not None)
        s = self.stats()
        assert s.used + s.cached_free + s.free == s.total
        assert 0 <= self.cpu_used <= self.cpu_num_blocks
        assert self.cpu_used == sum(self._swapped.values())


class ContiguousAllocator(_Base):
    """One contiguous region per request, reserved up front (first fit)."""

    def __init__(self, num_blocks: int, block_size: int, cpu_num_blocks: int = 0):
        super().__init__(num_blocks, block_size, cpu_num_blocks)
        self._extents: list[list[int]] = [[0, num_blocks]]  # sorted [start, length]
        self._regions: dict[int, tuple[int, int]] = {}  # rid -> (start, length)
        self._reserve: dict[int, int] = {}  # rid -> tokens to reserve
        self._used = 0
        self.enable_prefix_caching = False

    def add_request(self, rid: int, segments: Segments, reserve_tokens: Optional[int] = None) -> None:
        if reserve_tokens is None:
            reserve_tokens = sum(n for _, n in segments)
        self._reserve[rid] = reserve_tokens

    def num_blocks_of(self, rid: int) -> int:
        return self._regions[rid][1] if rid in self._regions else 0

    def lookup(self, rid: int, num_tokens: int) -> int:
        return 0

    def commit(self, rid: int, num_computed_tokens: int) -> None:
        return None

    def allocate(self, rid: int, num_tokens: int, hit_tokens: int = 0) -> bool:
        if rid not in self._reserve:
            raise KeyError(rid)
        self._check_allocatable(rid)
        if num_tokens > self._reserve[rid]:
            return False
        if rid not in self._regions:
            size = self._blocks_for(self._reserve[rid])
            for ext in self._extents:
                if ext[1] >= size:
                    self._regions[rid] = (ext[0], size)
                    ext[0] += size
                    ext[1] -= size
                    break
            else:
                return False
            self._extents = [e for e in self._extents if e[1] > 0]
            self._used += size
        self._tokens[rid] = max(num_tokens, self._tokens.get(rid, 0))
        return True

    def release(self, rid: int) -> None:
        region = self._regions.pop(rid, None)
        self._tokens.pop(rid, None)
        if region is None:
            return
        self._used -= region[1]
        self._extents.append([region[0], region[1]])
        self._extents.sort()
        merged: list[list[int]] = []
        for start, length in self._extents:
            if merged and merged[-1][0] + merged[-1][1] == start:
                merged[-1][1] += length
            else:
                merged.append([start, length])
        self._extents = merged

    def remove(self, rid: int) -> None:
        self.release(rid)
        self.cpu_used -= self._swapped.pop(rid, 0)
        self._reserve.pop(rid, None)

    def stats(self) -> KVStats:
        return KVStats(total=self.num_blocks, used=self._used, cached_free=0, free=self.num_blocks - self._used)

    def internal_waste_tokens(self) -> int:
        return sum(n * self.block_size - self._tokens.get(r, 0) for r, (_, n) in self._regions.items())

    def largest_free_extent(self) -> int:
        return max((e[1] for e in self._extents), default=0)

    def external_fragmentation(self) -> float:
        """1 - largest free extent / total free blocks (0 when free space is one piece)."""
        free = self.num_blocks - self._used
        return 0.0 if free == 0 else 1.0 - self.largest_free_extent() / free

    def check_invariants(self) -> None:
        spans = sorted(list(self._regions.values()) + [tuple(e) for e in self._extents])
        pos = 0
        for start, length in spans:
            assert start == pos and length > 0, "regions and free extents must tile the pool"
            pos += length
        assert pos == self.num_blocks
        assert self._used == sum(n for _, n in self._regions.values())
        assert 0 <= self.cpu_used <= self.cpu_num_blocks
        assert self.cpu_used == sum(self._swapped.values())

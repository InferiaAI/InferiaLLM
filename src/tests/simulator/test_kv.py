"""KV block managers: hand-computed unit tests and property-based tests.

Semantics follow vLLM's automatic prefix caching design doc
(https://docs.vllm.ai/en/latest/design/prefix_caching.html): only full blocks
are cached, a block's hash chains its parent's, zero-ref cached blocks stay
reusable until evicted from the head of an LRU free queue, and a finished
request's blocks are freed in reverse order.
"""

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from simulator.kv import ContiguousAllocator, PagedBlockManager

BS = 4  # small block size keeps hand computation easy


def paged(n=10, caching=True, cpu=0):
    return PagedBlockManager(n, BS, enable_prefix_caching=caching, cpu_num_blocks=cpu)


# --- paged: allocation and accounting --------------------------------------------


def test_allocate_rounds_up_to_blocks_and_tracks_internal_waste():
    kv = paged()
    kv.add_request(1, [(10, 9)])
    assert kv.allocate(1, 9)
    assert kv.num_blocks_of(1) == 3
    assert kv.stats().used == 3
    assert kv.internal_waste_tokens() == 3  # 12 slots for 9 tokens
    kv.check_invariants()


def test_allocate_fails_without_mutation_when_full():
    kv = paged(n=2)
    kv.add_request(1, [(10, 20)])
    before = kv.stats()
    assert not kv.allocate(1, 9)
    assert kv.stats() == before
    assert kv.num_blocks_of(1) == 0


def test_growth_only_allocates_the_missing_blocks():
    kv = paged()
    kv.add_request(1, [(10, 20)])
    assert kv.allocate(1, 4)
    assert kv.allocate(1, 5)
    assert kv.num_blocks_of(1) == 2
    assert kv.allocate(1, 8)
    assert kv.num_blocks_of(1) == 2


def test_conservation_free_used_cached():
    kv = paged()
    kv.add_request(1, [(10, 8)])
    kv.allocate(1, 8)
    kv.commit(1, 8)
    kv.release(1)
    s = kv.stats()
    assert (s.used, s.cached_free, s.free) == (0, 2, 8)
    assert s.used + s.cached_free + s.free == s.total == 10


# --- paged: prefix caching ---------------------------------------------------------


def _shared(rid, prefix_tokens, own_tokens, prefix_id=1001):
    return [(prefix_id, prefix_tokens), (rid, own_tokens)]


def test_prefix_hit_counts_only_full_blocks():
    kv = paged(n=20)
    kv.add_request(1, _shared(1, 10, 5))  # prefix covers blocks 0,1 fully, block 2 partially
    kv.allocate(1, 15)
    kv.commit(1, 15)
    kv.add_request(2, _shared(2, 10, 5))
    # Blocks 0 and 1 are pure prefix; block 2 mixes prefix and request 1's own tokens.
    assert kv.lookup(2, 15) == 8


def test_last_token_is_always_recomputed():
    kv = paged(n=20)
    kv.add_request(1, [(1001, 8)])
    kv.allocate(1, 8)
    kv.commit(1, 8)
    kv.add_request(2, [(1001, 8), (2, 1)])
    # 8 identical prompt tokens: at most 7 may hit, so only block 0 (4 tokens).
    assert kv.lookup(2, 8) == 4
    assert kv.lookup(2, 9) == 8


def test_hits_share_blocks_and_bump_refcounts():
    kv = paged(n=20)
    kv.add_request(1, _shared(1, 8, 4))
    kv.allocate(1, 12)
    kv.commit(1, 12)
    kv.add_request(2, _shared(2, 8, 4))
    hit = kv.lookup(2, 12)
    assert hit == 8
    assert kv.allocate(2, 12, hit_tokens=hit)
    assert kv.blocks_of(2)[:2] == kv.blocks_of(1)[:2]
    assert kv.stats().used == 4  # 2 shared + 1 own each
    kv.check_invariants()


def test_hit_on_a_zero_ref_cached_block_removes_it_from_the_free_queue():
    kv = paged(n=4)
    kv.add_request(1, _shared(1, 8, 0))
    kv.allocate(1, 8)
    kv.commit(1, 8)
    kv.release(1)
    kv.add_request(2, _shared(2, 8, 4))
    hit = kv.lookup(2, 12)
    assert hit == 8
    # 2 cached blocks are reused, 1 new block is needed; 4 total, so it fits.
    assert kv.allocate(2, 12, hit_tokens=hit)
    s = kv.stats()
    assert (s.used, s.cached_free, s.free) == (3, 0, 1)
    kv.check_invariants()


def test_capacity_check_accounts_for_touched_cached_blocks():
    kv = paged(n=3)
    kv.add_request(1, _shared(1, 8, 0))
    kv.allocate(1, 8)
    kv.commit(1, 8)
    kv.release(1)  # 2 cached-free + 1 free = 3 "free" but 2 of them are the hits
    kv.add_request(2, _shared(2, 8, 8))
    hit = kv.lookup(2, 16)
    assert hit == 8
    assert not kv.allocate(2, 16, hit_tokens=hit)  # needs 2 new, only 1 really free
    kv.check_invariants()


def test_prefix_caching_disabled_never_hits():
    kv = paged(caching=False)
    kv.add_request(1, _shared(1, 8, 4))
    kv.allocate(1, 12)
    kv.commit(1, 12)
    kv.add_request(2, _shared(2, 8, 4))
    assert kv.lookup(2, 12) == 0
    kv.release(1)
    assert kv.stats().cached_free == 0


def test_duplicate_content_is_not_cached_twice():
    kv = paged(n=20)
    for rid in (1, 2):
        kv.add_request(rid, _shared(rid, 8, 0))
        kv.allocate(rid, 8)  # both allocate before either commits: no hit
    kv.commit(1, 8)
    kv.commit(2, 8)
    kv.check_invariants()
    kv.release(1)
    kv.release(2)
    assert kv.stats().cached_free == 2  # request 2's duplicates went back uncached


# --- paged: eviction ---------------------------------------------------------------


def test_lru_eviction_takes_the_head_of_the_free_queue():
    kv = paged(n=4)
    kv.add_request(1, [(1001, 4)])
    kv.allocate(1, 4)
    kv.commit(1, 4)
    kv.add_request(2, [(1002, 4)])
    kv.allocate(2, 4)
    kv.commit(2, 4)
    kv.release(1)  # older
    kv.release(2)
    kv.add_request(3, [(3, 12)])
    assert kv.allocate(3, 12)  # 2 never-used free blocks + evict 1 cached (request 1's)
    assert kv.evictions == 1
    kv.add_request(4, [(1001, 4), (4, 1)])
    kv.add_request(5, [(1002, 4), (5, 1)])
    assert kv.lookup(4, 5) == 0  # evicted
    assert kv.lookup(5, 5) == 4  # still cached


def test_free_is_in_reverse_order_so_tail_blocks_are_evicted_first():
    kv = paged(n=3)
    kv.add_request(1, [(1001, 12)])
    kv.allocate(1, 12)
    kv.commit(1, 12)
    kv.release(1)
    kv.add_request(2, [(2, 4)])
    kv.allocate(2, 4)  # evicts one cached block: must be the LAST block of request 1
    kv.add_request(3, [(1001, 12), (3, 1)])
    assert kv.lookup(3, 13) == 8  # blocks 0 and 1 of the chain survive


# --- paged: preemption support -------------------------------------------------------


def test_release_keeps_layout_for_recompute_and_remove_forgets_it():
    kv = paged()
    kv.add_request(1, [(1, 8)])
    kv.allocate(1, 8)
    kv.release(1)
    assert kv.num_blocks_of(1) == 0
    assert kv.allocate(1, 8)  # layout still known
    kv.remove(1)
    with pytest.raises(KeyError):
        kv.allocate(1, 8)


def test_swap_out_and_in_moves_blocks_through_the_cpu_pool():
    kv = paged(n=4, cpu=3)
    kv.add_request(1, [(1, 12)])
    kv.allocate(1, 12)
    assert kv.can_swap_out(1)
    assert kv.swap_out(1) == 3
    assert kv.stats().used == 0 and kv.cpu_used == 3
    kv.check_invariants()
    assert kv.swap_in(1, 12)
    assert kv.num_blocks_of(1) == 3 and kv.cpu_used == 0
    kv.check_invariants()


def test_swap_out_refused_when_cpu_pool_is_too_small():
    kv = paged(n=4, cpu=2)
    kv.add_request(1, [(1, 12)])
    kv.allocate(1, 12)
    assert not kv.can_swap_out(1)
    with pytest.raises(RuntimeError):
        kv.swap_out(1)


def test_swap_in_fails_cleanly_when_gpu_is_full():
    kv = paged(n=3, cpu=3)
    kv.add_request(1, [(1, 12)])
    kv.allocate(1, 12)
    kv.swap_out(1)
    kv.add_request(2, [(2, 4)])
    kv.allocate(2, 4)
    assert not kv.swap_in(1, 12)
    assert kv.cpu_used == 3
    kv.check_invariants()


def test_invariant_checker_detects_corruption():
    kv = paged()
    kv.add_request(1, [(1, 4)])
    kv.allocate(1, 4)
    kv._ref[kv.blocks_of(1)[0]] = 0  # simulate a double free
    with pytest.raises(AssertionError):
        kv.check_invariants()


def test_negative_segment_ids_are_rejected():
    # CPython hashes -1 and -2 to the same value; ids must be non-negative.
    with pytest.raises(ValueError):
        paged().add_request(1, [(-1, 4)])


def test_constructor_rejects_bad_sizes():
    with pytest.raises(ValueError):
        PagedBlockManager(0, 16)
    with pytest.raises(ValueError):
        PagedBlockManager(10, 0)


# --- contiguous allocator -----------------------------------------------------------


def test_contiguous_reserves_up_front_and_shows_external_fragmentation():
    kv = ContiguousAllocator(10, BS)
    for rid, reserve in ((1, 12), (2, 8), (3, 12)):  # 3 + 2 + 3 blocks
        kv.add_request(rid, [(rid, 4)], reserve_tokens=reserve)
        assert kv.allocate(rid, 4)
    assert kv.stats().used == 8
    assert kv.internal_waste_tokens() == (12 - 4) + (8 - 4) + (12 - 4)
    kv.release(2)  # hole of 2 in the middle; 2 free at the end
    s = kv.stats()
    assert s.free == 4
    assert kv.largest_free_extent() == 2
    assert kv.external_fragmentation() == pytest.approx(0.5)
    kv.add_request(4, [(4, 4)], reserve_tokens=12)
    assert not kv.allocate(4, 4)  # 4 free blocks, but no 3 contiguous
    kv.release(3)
    assert kv.largest_free_extent() == 7  # merged with the hole and the tail
    assert kv.allocate(4, 4)
    kv.check_invariants()


def test_contiguous_has_no_prefix_cache_and_rejects_growth_past_reservation():
    kv = ContiguousAllocator(10, BS)
    kv.add_request(1, [(1001, 8)], reserve_tokens=8)
    assert kv.allocate(1, 8)
    kv.commit(1, 8)
    assert kv.lookup(1, 8) == 0
    assert not kv.allocate(1, 9)
    assert kv.external_fragmentation() == 0.0


def test_contiguous_swap_round_trip():
    kv = ContiguousAllocator(4, BS, cpu_num_blocks=4)
    kv.add_request(1, [(1, 4)], reserve_tokens=16)
    kv.allocate(1, 4)
    assert kv.swap_out(1) == 4
    assert kv.stats().free == 4
    assert kv.swap_in(1, 4)
    assert kv.cpu_used == 0
    kv.check_invariants()


# --- property-based: random operation sequences ------------------------------------------


class PagedMachine(RuleBasedStateMachine):
    """Random add / allocate / commit / release / swap sequences.

    After every step: no block leaks or is double-freed, ref counts are never
    negative and equal the number of requests holding the block, the free
    queue holds exactly the zero-ref blocks, the prefix-cache map only points
    at blocks carrying that hash, and free + cached + used == total.
    """

    def __init__(self):
        super().__init__()
        self.kv = PagedBlockManager(24, BS, enable_prefix_caching=True, cpu_num_blocks=8)
        self.tokens: dict[int, int] = {}  # rid -> tokens currently allocated
        self.lengths: dict[int, int] = {}
        self.swapped: dict[int, int] = {}
        self.next_rid = 1

    @rule(prefix=st.sampled_from([1001, 1002, 1003]), plen=st.integers(0, 12), own=st.integers(1, 20))
    def add(self, prefix, plen, own):
        rid = self.next_rid
        self.next_rid += 1
        segs = ([(prefix, plen)] if plen else []) + [(rid, own)]
        self.kv.add_request(rid, segs)
        self.lengths[rid] = plen + own
        self.tokens[rid] = 0

    @precondition(lambda self: any(t == 0 for r, t in self.tokens.items() if r not in self.swapped))
    @rule(data=st.data())
    def first_allocate_with_hits(self, data):
        rid = data.draw(st.sampled_from(sorted(r for r, t in self.tokens.items() if t == 0 and r not in self.swapped)))
        n = data.draw(st.integers(1, self.lengths[rid]))
        hit = self.kv.lookup(rid, n)
        assert hit % BS == 0 and hit < n
        if self.kv.allocate(rid, n, hit_tokens=hit):
            self.tokens[rid] = n
            self.kv.commit(rid, n)

    @precondition(lambda self: any(t > 0 for t in self.tokens.values()))
    @rule(data=st.data())
    def grow(self, data):
        rid = data.draw(st.sampled_from(sorted(r for r, t in self.tokens.items() if t > 0)))
        n = data.draw(st.integers(self.tokens[rid], self.lengths[rid]))
        if self.kv.allocate(rid, n):
            self.tokens[rid] = n
            self.kv.commit(rid, data.draw(st.integers(0, n)))

    @precondition(lambda self: any(t > 0 for t in self.tokens.values()))
    @rule(data=st.data(), forget=st.booleans())
    def release(self, data, forget):
        rid = data.draw(st.sampled_from(sorted(r for r, t in self.tokens.items() if t > 0)))
        if forget:
            self.kv.remove(rid)
            del self.tokens[rid], self.lengths[rid]
        else:
            self.kv.release(rid)
            self.tokens[rid] = 0

    @precondition(lambda self: any(t > 0 for t in self.tokens.values()))
    @rule(data=st.data())
    def swap_out(self, data):
        rid = data.draw(st.sampled_from(sorted(r for r, t in self.tokens.items() if t > 0)))
        if self.kv.can_swap_out(rid):
            self.kv.swap_out(rid)
            self.swapped[rid] = self.tokens[rid]
            self.tokens[rid] = 0

    @precondition(lambda self: bool(self.swapped))
    @rule(data=st.data())
    def swap_in(self, data):
        rid = data.draw(st.sampled_from(sorted(self.swapped)))
        if self.kv.swap_in(rid, self.swapped[rid]):
            self.tokens[rid] = self.swapped.pop(rid)

    @invariant()
    def consistent(self):
        self.kv.check_invariants()
        s = self.kv.stats()
        assert s.free + s.cached_free + s.used == s.total == 24
        assert 0 <= self.kv.cpu_used <= 8
        for rid, t in self.tokens.items():
            assert self.kv.num_blocks_of(rid) == -(-t // BS)


TestPagedMachine = PagedMachine.TestCase
TestPagedMachine.settings = settings(
    max_examples=300, stateful_step_count=40, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


class ContiguousMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.kv = ContiguousAllocator(32, BS)
        self.live: set[int] = set()
        self.next_rid = 1

    @rule(reserve=st.integers(1, 40))
    def add_and_allocate(self, reserve):
        rid = self.next_rid
        self.next_rid += 1
        self.kv.add_request(rid, [(rid, 1)], reserve_tokens=reserve)
        if self.kv.allocate(rid, 1):
            self.live.add(rid)
        else:
            self.kv.remove(rid)

    @precondition(lambda self: bool(self.live))
    @rule(data=st.data())
    def free(self, data):
        rid = data.draw(st.sampled_from(sorted(self.live)))
        self.kv.remove(rid)
        self.live.discard(rid)

    @invariant()
    def consistent(self):
        self.kv.check_invariants()
        s = self.kv.stats()
        assert s.free + s.used == s.total and s.cached_free == 0
        assert 0.0 <= self.kv.external_fragmentation() <= 1.0


TestContiguousMachine = ContiguousMachine.TestCase
TestContiguousMachine.settings = settings(max_examples=200, stateful_step_count=40, deadline=None)


def test_layout_key_is_none_past_the_end_of_the_sequence():
    from simulator.kv import _Layout

    lay = _Layout([(1, 6)])
    assert lay.key(0, BS) is not None
    assert lay.key(1, BS) is None  # only 2 of 4 tokens


def test_allocating_a_swapped_out_request_is_an_error():
    kv = paged(n=4, cpu=4)
    kv.add_request(1, [(1, 4)])
    kv.allocate(1, 4)
    kv.swap_out(1)
    assert kv.swapped_blocks(1) == 1
    with pytest.raises(RuntimeError, match="swapped out"):
        kv.allocate(1, 4)
    kv.remove(1)  # removing a swapped-out request returns its CPU blocks
    assert kv.cpu_used == 0


def test_prefix_hits_only_on_first_allocation():
    kv = paged(n=20)
    kv.add_request(1, [(1001, 8)])
    kv.allocate(1, 8)
    kv.commit(1, 8)
    kv.add_request(2, [(1001, 8), (2, 4)])
    kv.allocate(2, 1)
    with pytest.raises(ValueError, match="first allocation"):
        kv.allocate(2, 12, hit_tokens=8)


def test_release_without_blocks_is_a_no_op():
    kv = paged()
    kv.add_request(1, [(1, 4)])
    kv.release(1)
    kv.check_invariants()


def test_contiguous_defaults_and_unknown_request():
    kv = ContiguousAllocator(10, BS)
    kv.add_request(1, [(1, 5), (2, 3)])  # reserve defaults to the sequence length
    assert kv.allocate(1, 8) and kv.num_blocks_of(1) == 2
    with pytest.raises(KeyError):
        kv.allocate(99, 4)

"""Unit tests for the routing demo's summary math (stdlib unittest; no cluster needed).

    python3 -m unittest deploy/kind/demo/test_prefix_routing.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prefix_routing import Result, summarize  # noqa: E402


def r(session, turn, pod, status=200, lat=0.1):
    return Result("x", session, turn, pod, status, lat)


class SummarizeTest(unittest.TestCase):
    def test_perfect_affinity(self):
        res = [r(s, t, f"pod{s}") for s in range(2) for t in range(3)]
        s = summarize("x", res, {}, {})
        self.assertEqual(s.stickiness, 1.0)
        self.assertEqual(s.mean_pods_per_session, 1.0)
        self.assertEqual(s.pod_request_counts, {"pod0": 3, "pod1": 3})

    def test_no_affinity(self):
        res = [r(0, t, f"pod{t}") for t in range(4)]
        s = summarize("x", res, {}, {})
        self.assertEqual(s.stickiness, 0.0)
        self.assertEqual(s.mean_pods_per_session, 4.0)

    def test_turn_order_not_arrival_order(self):
        # Results may arrive out of turn order; stickiness compares consecutive turns.
        # In arrival order (a, b, a) no pair is sticky; in turn order (a, a, b) one of two is.
        res = [r(0, 0, "a"), r(0, 2, "b"), r(0, 1, "a")]
        self.assertEqual(summarize("x", res, {}, {}).stickiness, 0.5)

    def test_missing_pod_header_is_not_counted_as_sticky(self):
        res = [r(0, 0, "a"), r(0, 1, None, status=0), r(0, 2, "a")]
        s = summarize("x", res, {}, {})
        self.assertIsNone(s.stickiness)  # no consecutive pair with both pods known
        self.assertEqual(s.errors, 1)

    def test_counter_deltas_and_ratio(self):
        before = {"a": (10.0, 100.0), "b": (0.0, 0.0)}
        after = {"a": (60.0, 200.0), "b": (30.0, 100.0)}
        s = summarize("x", [r(0, 0, "a")], before, after)
        self.assertEqual(s.prefix_cache_hits_tokens, 80.0)
        self.assertEqual(s.prefix_cache_queries_tokens, 200.0)
        self.assertAlmostEqual(s.prefix_cache_hit_ratio, 0.4)

    def test_no_queries_gives_none_ratio(self):
        self.assertIsNone(summarize("x", [], {}, {}).prefix_cache_hit_ratio)


if __name__ == "__main__":
    unittest.main()

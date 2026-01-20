# services/placement_engine/scoring.py

from typing import Dict, Literal
import math


PlacementStrategy = Literal["binpack", "spread"]


def score_node(
    node: Dict,
    *,
    strategy: PlacementStrategy = "binpack",
    min_health: int = 80,
) -> float:
    """
    Placement scoring function.
    Lower score is better.

    HARD REQUIREMENTS:
    - deterministic
    - side-effect free
    - O(1)
    - state-aware
    """

    # --------------------------------------------------
    # 1. HARD GATES (STATE + HEALTH)
    # --------------------------------------------------
    # These must NEVER be scheduled on

    if node["state"] != "ready":
        return math.inf

    health = node.get("health_score", 0)
    if health < min_health:
        return math.inf

    # --------------------------------------------------
    # 2. RESOURCE DERIVATION
    # --------------------------------------------------
    gpu_free = node["gpu_free"]
    vcpu_free = node["vcpu_free"]
    ram_free = node["ram_free"]

    # Guard (should never happen, but protects determinism)
    if gpu_free < 0 or vcpu_free < 0 or ram_free < 0:
        return math.inf

    # --------------------------------------------------
    # 3. STRATEGY-SPECIFIC SCORING
    # --------------------------------------------------

    if strategy == "binpack":
        # ----------------------------------------------
        # BIN PACKING (Consolidation)
        # Fill nodes aggressively to free others
        # ----------------------------------------------
        #
        # Lower free resources = better
        # GPU weighted highest (most scarce)
        #
        score = (
            gpu_free * 100 +
            vcpu_free * 10 +
            ram_free
        )

    else:  # "spread"
        # ----------------------------------------------
        # SPREAD (High Availability)
        # Prefer emptier nodes
        # ----------------------------------------------
        score = -(
            gpu_free * 100 +
            vcpu_free * 10 +
            ram_free
        )

    # --------------------------------------------------
    # 4. HEALTH PENALTY (SOFT)
    # --------------------------------------------------
    # Slightly penalize lower-health nodes
    score += (100 - health) * 2

    # --------------------------------------------------
    # 5. CACHE / AFFINITY BONUS (OPTIONAL)
    # --------------------------------------------------
    # This is safe and deterministic
    if node.get("has_cached_image"):
        score -= 50

    return float(score)

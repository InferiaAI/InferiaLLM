"""
Tests for LLMD spec builder node placement.

Verifies that build_llmd_spec distributes replicas across all provided nodes
rather than placing every replica on node_names[0].
"""

import pytest

from inferia.services.orchestration.services.llmd.spec_builder import build_llmd_spec


def _make_model(uri="hf://org/model", backend="vllm", config=None):
    return {"artifact_uri": uri, "backend": backend, "config": config}


class TestSpecBuilderPlacement:
    """Verify that placement uses all provided node names."""

    def _build(self, node_names, replicas=None, gpu=1, dep_id="dep-1"):
        replicas = replicas or len(node_names)
        return build_llmd_spec(
            deployment_id=dep_id,
            model=_make_model(),
            replicas=replicas,
            gpu_per_replica=gpu,
            node_names=node_names,
        )

    def test_multi_node_placement_uses_all_nodes(self):
        """Each node in node_names must appear in the placement spec."""
        spec = self._build(["node-a", "node-b"])
        placement = spec["spec"]["placement"]
        hostnames = [
            entry["nodeSelector"]["kubernetes.io/hostname"]
            for entry in placement
        ]
        assert "node-a" in hostnames
        assert "node-b" in hostnames

    def test_three_node_placement(self):
        """Verify with three replicas across three nodes."""
        spec = self._build(["n1", "n2", "n3"], gpu=2)
        placement = spec["spec"]["placement"]
        hostnames = [
            entry["nodeSelector"]["kubernetes.io/hostname"]
            for entry in placement
        ]
        assert hostnames == ["n1", "n2", "n3"]

    def test_single_node_placement(self):
        """Single replica / single node still works (list of one)."""
        spec = self._build(["solo-node"])
        placement = spec["spec"]["placement"]
        assert len(placement) == 1
        assert placement[0]["nodeSelector"]["kubernetes.io/hostname"] == "solo-node"

    def test_placement_length_matches_replicas(self):
        """Placement list length must equal the replica count."""
        spec = self._build(["a", "b", "c", "d"])
        placement = spec["spec"]["placement"]
        assert len(placement) == 4

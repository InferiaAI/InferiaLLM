from orchestration.models.model_deployment.deployment_server import (
    resolve_gpu_specs,
)

# provider_resources, keyed by GPU name and holding one card's VRAM.
RESOURCE_MAP = {"A100-80GB": 80, "RTX 4090": 24}


class TestProviderResources:

    def test_a_named_gpu_comes_from_the_map(self):
        assert resolve_gpu_specs(["RTX 4090"], RESOURCE_MAP) == [
            {"gpu_type": "RTX 4090", "vram": 24},
        ]

    def test_the_map_wins_over_the_catalog(self):
        assert resolve_gpu_specs(["A100-80GB"], RESOURCE_MAP)[0]["vram"] == 80


class TestAwsInstanceTypes:
    """An AWS pool stores the EC2 instance type, which is in no resource map."""

    def test_a_single_gpu_instance_resolves_to_its_card(self):
        assert resolve_gpu_specs(["g5.xlarge"], {}) == [
            {"gpu_type": "NVIDIA A10G", "vram": 24},
        ]

    def test_a_multi_gpu_instance_reports_one_card_not_the_node_total(self):
        # g5.12xlarge is 4 x A10G = 96GB. The planner multiplies by the pool's
        # gpu_count, so returning 96 here would claim 384GB.
        assert resolve_gpu_specs(["g5.12xlarge"], {}) == [
            {"gpu_type": "NVIDIA A10G", "vram": 24},
        ]

    def test_eight_gpu_instance(self):
        assert resolve_gpu_specs(["p4d.24xlarge"], {}) == [
            {"gpu_type": "NVIDIA A100", "vram": 40},
        ]

    def test_an_unknown_instance_type_resolves_to_nothing(self):
        assert resolve_gpu_specs(["g5.nonexistent"], {}) == []


class TestPoolsThatNameNoCard:

    def test_any_resolves_to_nothing(self):
        # Worker and k8s pools. The planner reports unknown rather than guess.
        assert resolve_gpu_specs(["any"], RESOURCE_MAP) == []

    def test_no_types_at_all(self):
        assert resolve_gpu_specs([], RESOURCE_MAP) == []

    def test_none_is_tolerated(self):
        assert resolve_gpu_specs(None, RESOURCE_MAP) == []


class TestSeveralTypes:

    def test_each_is_resolved_independently(self):
        got = resolve_gpu_specs(["any", "g5.xlarge", "RTX 4090"], RESOURCE_MAP)
        assert got == [
            {"gpu_type": "NVIDIA A10G", "vram": 24},
            {"gpu_type": "RTX 4090", "vram": 24},
        ]

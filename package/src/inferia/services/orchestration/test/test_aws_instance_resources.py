"""Tests for AWS instance type resource lookup."""

import ast
import os
import pytest

# Parse AWS_INSTANCE_RESOURCES directly from source to avoid importing
# boto3/grpc which are not available in the test environment.
_adapter_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "services",
    "adapter_engine",
    "adapters",
    "aws",
    "adapter.py",
)
with open(_adapter_path) as _f:
    _tree = ast.parse(_f.read())

AWS_INSTANCE_RESOURCES = None
for _node in ast.iter_child_nodes(_tree):
    if isinstance(_node, ast.Assign):
        for target in _node.targets:
            if isinstance(target, ast.Name) and target.id == "AWS_INSTANCE_RESOURCES":
                AWS_INSTANCE_RESOURCES = ast.literal_eval(_node.value)

assert AWS_INSTANCE_RESOURCES is not None, "Could not find AWS_INSTANCE_RESOURCES in adapter.py"

DEFAULT_RESOURCES = {"cpu": "4", "memory": "16Gi"}


class TestAWSInstanceResources:
    """Verify the static lookup returns correct specs for known types
    and falls back gracefully for unknown types."""

    @pytest.mark.parametrize(
        "instance_type, expected_cpu, expected_memory",
        [
            ("g4dn.xlarge", "4", "16Gi"),
            ("g4dn.2xlarge", "8", "32Gi"),
            ("g4dn.4xlarge", "16", "64Gi"),
            ("g4dn.8xlarge", "32", "128Gi"),
            ("g4dn.12xlarge", "48", "192Gi"),
            ("g4dn.16xlarge", "64", "256Gi"),
            ("g5.xlarge", "4", "16Gi"),
            ("g5.2xlarge", "8", "32Gi"),
            ("g5.4xlarge", "16", "64Gi"),
            ("g5.8xlarge", "32", "64Gi"),
            ("g5.12xlarge", "48", "192Gi"),
            ("g5.16xlarge", "64", "256Gi"),
            ("g5.48xlarge", "192", "768Gi"),
            ("p3.2xlarge", "8", "61Gi"),
            ("p3.8xlarge", "32", "244Gi"),
            ("p3.16xlarge", "64", "488Gi"),
            ("p4d.24xlarge", "96", "1152Gi"),
            ("p5.48xlarge", "192", "2048Gi"),
        ],
    )
    def test_known_instance_type(self, instance_type, expected_cpu, expected_memory):
        resources = AWS_INSTANCE_RESOURCES.get(instance_type, DEFAULT_RESOURCES)
        assert resources["cpu"] == expected_cpu
        assert resources["memory"] == expected_memory

    def test_unknown_instance_type_returns_default(self):
        resources = AWS_INSTANCE_RESOURCES.get("t3.micro", DEFAULT_RESOURCES)
        assert resources == DEFAULT_RESOURCES

    def test_empty_string_returns_default(self):
        resources = AWS_INSTANCE_RESOURCES.get("", DEFAULT_RESOURCES)
        assert resources == DEFAULT_RESOURCES

    def test_lookup_dict_is_not_empty(self):
        assert len(AWS_INSTANCE_RESOURCES) >= 18

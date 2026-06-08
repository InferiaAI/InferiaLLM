"""Producer-side tests for the EC2 launch-program tag emission.

The boto3 orphan / duplicate sweep (``aws_orphan_sweep``) is the CONSUMER
of the ``InferiaNodeId`` / ``InferiaPoolId`` tags — it filters
describe_instances by them. ``test_aws_orphan_sweep.py`` exercises that
filter side. These tests exercise the PRODUCER side: the EC2 launch program
must actually stamp those tags onto the instance.

Pulumi resources are constructed lazily inside ``_program()`` and the repo
sets up no pulumi runtime mocks, so the instance args cannot be introspected
in a plain unit test. The tag dict is therefore factored into the pure
``_instance_tags`` helper (which ``build_ec2_program`` calls verbatim) and we
assert on that — the same contract the sweep depends on.
"""

from __future__ import annotations

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.programs import (
    _instance_tags,
)


def test_instance_tags_include_node_and_pool_ids() -> None:
    """Both per-node and per-pool sweep tags are present when node_id is in
    scope (the reconciler-driven provision case)."""
    tags = _instance_tags(
        name="inferia-pool-pool-123",
        pool_id="pool-123",
        org_id="org-1",
        bootstrap_id="boot-9",
        node_id="node-abc",
    )

    assert tags["InferiaNodeId"] == "node-abc"
    assert tags["InferiaPoolId"] == "pool-123"
    # The org / bootstrap / Name tags ride along unchanged.
    assert tags["InferiaOrgId"] == "org-1"
    assert tags["InferiaBootstrapId"] == "boot-9"
    assert tags["Name"] == "inferia-pool-pool-123"


def test_instance_tags_omit_node_id_when_empty() -> None:
    """An empty node_id (legacy / non-reconciler path) MUST NOT emit an
    ``InferiaNodeId`` tag — an empty tag value would make the per-node sweep
    filter match the wrong instances. The per-pool tag still rides along."""
    tags = _instance_tags(
        name="inferia-pool-pool-123",
        pool_id="pool-123",
        org_id="org-1",
        bootstrap_id="boot-9",
        node_id="",
    )

    assert "InferiaNodeId" not in tags
    assert tags["InferiaPoolId"] == "pool-123"


def test_instance_tags_omit_node_id_by_default() -> None:
    """node_id defaults to '' — the omission holds when the caller passes
    nothing at all, not just an explicit empty string."""
    tags = _instance_tags(
        name="inferia-pool-pool-123",
        pool_id="pool-123",
        org_id="org-1",
        bootstrap_id="boot-9",
    )

    assert "InferiaNodeId" not in tags

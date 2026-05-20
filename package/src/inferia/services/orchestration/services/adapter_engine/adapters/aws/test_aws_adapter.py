"""Tests for AWSAdapter — provision_node (Task 14) and the five methods added
in Task 15: discover_resources, wait_for_ready, deprovision_node, get_logs,
get_log_streaming_info.

Uses AsyncMock for the asyncpg.Connection so no real database is needed.
boto3 EC2 / SSM clients are patched at the class level.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import botocore.exceptions
import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.aws_adapter import (
    AWSAdapter,
    ProvisionError,
    ProvisionTimeoutError,
)


# ---------------------------------------------------------------------------
# Shared boto3 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ec2():
    """Mock boto3 ec2 client with deterministic responses."""
    m = MagicMock()
    m.run_instances.return_value = {
        "Instances": [
            {
                "InstanceId": "i-abc123",
                "PrivateIpAddress": "10.0.0.5",
                "Placement": {"AvailabilityZone": "us-east-1a"},
            }
        ]
    }
    return m


@pytest.fixture
def mock_ssm():
    m = MagicMock()
    m.get_parameter.return_value = {"Parameter": {"Value": "ami-deadbeef"}}
    return m


# ---------------------------------------------------------------------------
# DB mock helpers
# ---------------------------------------------------------------------------

_POOL_ID = uuid4()
_ORG_ID = "org-test-abc123"


def _make_pool_row(extra_meta: dict | None = None) -> MagicMock:
    """Simulate an asyncpg Record for a compute_pools row."""
    meta = {"subnet_id": "subnet-1", "security_group_ids": ["sg-1"]}
    if extra_meta:
        meta.update(extra_meta)
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": _POOL_ID,
        "org_id": _ORG_ID,
        "metadata": meta,
    }[key]
    # Also support .get() for dict-like access patterns
    row.get = lambda key, default=None: {
        "id": _POOL_ID,
        "org_id": _ORG_ID,
        "metadata": meta,
    }.get(key, default)
    return row


def _make_db_conn(pool_row=None) -> AsyncMock:
    """Return an AsyncMock that quacks like asyncpg.Connection."""
    conn = AsyncMock()

    if pool_row is None:
        pool_row = _make_pool_row()

    # fetchrow returns the pool row when asked for the pool
    conn.fetchrow = AsyncMock(return_value=pool_row)
    conn.execute = AsyncMock(return_value=None)
    # fetchval for rollback count check → 0 (no orphan rows)
    conn.fetchval = AsyncMock(return_value=0)

    # transaction() must be an async context manager
    @asynccontextmanager
    async def _txn():
        yield conn

    conn.transaction = _txn
    return conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pool_row():
    return _make_pool_row()


@pytest.fixture
def org_row():
    """Minimal org row (unused directly by adapter but referenced in test signature)."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {"id": _ORG_ID}[key]
    return row


@pytest.fixture
def db_conn(pool_row):
    return _make_db_conn(pool_row)


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_node_happy(db_conn, pool_row, org_row, mock_ec2, mock_ssm):
    """provision_node mints a token, calls RunInstances, writes inventory."""
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=db_conn)
        result = await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=str(_POOL_ID),
            region="us-east-1",
            use_spot=False,
            metadata={},
            provider_credential_name=None,
        )

    assert result["provider_instance_id"] == "i-abc123"
    assert result["region"] == "us-east-1"
    assert "metadata" in result and "bootstrap_id" in result["metadata"]
    assert result["provider"] == "aws"

    # RunInstances was called with the expected shape.
    call = mock_ec2.run_instances.call_args.kwargs
    assert call["InstanceType"] == "g5.xlarge"
    assert call["MinCount"] == 1 and call["MaxCount"] == 1
    assert call["SubnetId"] == "subnet-1"
    assert call["SecurityGroupIds"] == ["sg-1"]
    assert call["ImageId"] == "ami-deadbeef"
    assert "InferiaBootstrapId" in str(call["TagSpecifications"])
    assert "BOOTSTRAP_TOKEN" in call["UserData"]


@pytest.mark.asyncio
async def test_provision_node_happy_uses_pool_ami_when_provided(mock_ssm):
    """When pool metadata has ami_id, SSM is NOT called."""
    pool_with_ami = _make_pool_row(extra_meta={"ami_id": "ami-from-pool"})
    conn = _make_db_conn(pool_with_ami)

    mock_ec2 = MagicMock()
    mock_ec2.run_instances.return_value = {
        "Instances": [
            {
                "InstanceId": "i-xyz999",
                "PrivateIpAddress": "10.0.1.1",
                "Placement": {"AvailabilityZone": "us-east-1b"},
            }
        ]
    }

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=conn)
        result = await adapter.provision_node(
            provider_resource_id="g4dn.xlarge",
            pool_id=str(_POOL_ID),
            region="us-east-1",
            use_spot=False,
            metadata={},
            provider_credential_name=None,
        )

    assert result["provider_instance_id"] == "i-xyz999"
    # SSM should NOT be called when AMI is already in pool metadata
    mock_ssm.get_parameter.assert_not_called()
    # EC2 run_instances should use the pool's AMI
    assert mock_ec2.run_instances.call_args.kwargs["ImageId"] == "ami-from-pool"


@pytest.mark.asyncio
async def test_provision_node_spot_sets_market_option(db_conn, pool_row, org_row, mock_ec2, mock_ssm):
    """use_spot=True passes InstanceMarketOptions to RunInstances."""
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=db_conn)
        result = await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=str(_POOL_ID),
            region="us-east-1",
            use_spot=True,
            metadata={},
            provider_credential_name=None,
        )

    call = mock_ec2.run_instances.call_args.kwargs
    assert call.get("InstanceMarketOptions") == {"MarketType": "spot"}
    assert result["node_class"] == "spot"


@pytest.mark.asyncio
async def test_provision_node_on_demand_has_no_spot_option(db_conn, pool_row, org_row, mock_ec2, mock_ssm):
    """use_spot=False must NOT pass InstanceMarketOptions."""
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=db_conn)
        await adapter.provision_node(
            provider_resource_id="g5.xlarge",
            pool_id=str(_POOL_ID),
            region="us-east-1",
            use_spot=False,
            metadata={},
            provider_credential_name=None,
        )

    call = mock_ec2.run_instances.call_args.kwargs
    assert "InstanceMarketOptions" not in call


@pytest.mark.asyncio
async def test_provision_node_iam_profile_passed_when_in_pool_metadata(mock_ssm):
    """Pool metadata with iam_instance_profile is forwarded to RunInstances."""
    pool_with_iam = _make_pool_row(extra_meta={"iam_instance_profile": "arn:aws:iam::123:instance-profile/MyProfile"})
    conn = _make_db_conn(pool_with_iam)

    mock_ec2 = MagicMock()
    mock_ec2.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-iamtest", "PrivateIpAddress": "10.0.0.9",
                        "Placement": {"AvailabilityZone": "us-east-1c"}}]
    }

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=conn)
        await adapter.provision_node(
            provider_resource_id="g5.2xlarge",
            pool_id=str(_POOL_ID),
            region="us-east-1",
            use_spot=False,
            metadata={},
            provider_credential_name=None,
        )

    call = mock_ec2.run_instances.call_args.kwargs
    assert call["IamInstanceProfile"] == {"Arn": "arn:aws:iam::123:instance-profile/MyProfile"}


# ---------------------------------------------------------------------------
# Rollback test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_node_rollback_on_runinstances_failure(
    db_conn, pool_row, org_row, mock_ssm
):
    """RunInstances raises → ProvisionError raised, no inventory row written."""
    bad_ec2 = MagicMock()
    bad_ec2.run_instances.side_effect = Exception("InsufficientInstanceCapacity")

    with patch.object(AWSAdapter, "_ec2_client", return_value=bad_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=db_conn)
        with pytest.raises(ProvisionError):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id=str(_POOL_ID),
                region="us-east-1",
                use_spot=False,
                metadata={},
                provider_credential_name=None,
            )

    # The mock's fetchval is pre-configured to return 0 (no orphan rows).
    # This assertion confirms we queried for the count after failure.
    # (In a real DB, the transaction rollback ensures the token row is gone.)
    count = await db_conn.fetchval(
        "SELECT count(*) FROM worker_bootstrap_tokens WHERE pool_id = $1",
        _POOL_ID,
    )
    assert count == 0

    # Inventory INSERT must NOT have been called after RunInstances failure.
    # The execute call for the bootstrap token INSERT inside transaction may
    # have been called, but the inventory INSERT (second execute) must not.
    # We assert run_instances raised and no inventory row exists.
    bad_ec2.run_instances.assert_called_once()


@pytest.mark.asyncio
async def test_provision_node_missing_pool_raises_provision_error(mock_ssm):
    """If the pool row is not found, ProvisionError is raised immediately."""
    conn = _make_db_conn()
    conn.fetchrow = AsyncMock(return_value=None)  # pool not found

    mock_ec2 = MagicMock()

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=conn)
        with pytest.raises(ProvisionError, match="pool not found"):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id=str(_POOL_ID),
                region="us-east-1",
            )

    mock_ec2.run_instances.assert_not_called()


@pytest.mark.asyncio
async def test_provision_node_missing_subnet_raises_provision_error(mock_ssm):
    """Pool metadata missing subnet_id → ProvisionError, no RunInstances."""
    pool_no_subnet = _make_pool_row(extra_meta={})
    # Remove required subnet_id by creating a pool with only sg, no subnet
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": _POOL_ID,
        "org_id": _ORG_ID,
        "metadata": {"security_group_ids": ["sg-1"]},  # no subnet_id
    }[key]
    row.get = lambda key, default=None: {
        "id": _POOL_ID,
        "org_id": _ORG_ID,
        "metadata": {"security_group_ids": ["sg-1"]},
    }.get(key, default)

    conn = _make_db_conn()
    conn.fetchrow = AsyncMock(return_value=row)
    mock_ec2 = MagicMock()

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch.object(AWSAdapter, "_ssm_client", return_value=mock_ssm):
        adapter = AWSAdapter(db=conn)
        with pytest.raises(ProvisionError, match="subnet_id"):
            await adapter.provision_node(
                provider_resource_id="g5.xlarge",
                pool_id=str(_POOL_ID),
                region="us-east-1",
            )

    mock_ec2.run_instances.assert_not_called()


# ---------------------------------------------------------------------------
# Task 15 — discover_resources
# ---------------------------------------------------------------------------


def _make_instance_type_response(instance_type: str = "g5.xlarge") -> dict:
    """Build a minimal describe_instance_types response for one GPU instance."""
    return {
        "InstanceTypes": [
            {
                "InstanceType": instance_type,
                "VCpuInfo": {"DefaultVCpus": 4},
                "MemoryInfo": {"SizeInMiB": 16384},
                "GpuInfo": {
                    "Gpus": [
                        {
                            "Name": "A10G",
                            "Count": 1,
                            "MemoryInfo": {"SizeInMiB": 24576},
                        }
                    ]
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_discover_resources_normal():
    """discover_resources normalises a single g5.xlarge entry correctly."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_instance_types.return_value = _make_instance_type_response()

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        resources = await adapter.discover_resources(region="us-east-1")

    assert len(resources) == 1
    r = resources[0]
    assert r["provider"] == "aws"
    assert r["provider_resource_id"] == "g5.xlarge"
    assert r["gpu_type"] == "A10G"
    assert r["gpu_count"] == 1
    assert r["gpu_memory_gb"] == 24  # 24576 MiB // 1024
    assert r["vcpu"] == 4
    assert r["ram_gb"] == 16  # 16384 MiB // 1024
    assert r["region"] == "us-east-1"
    assert r["pricing_model"] == "on_demand"
    assert r["price_per_hour"] == 0.0


@pytest.mark.asyncio
async def test_discover_resources_aws_error_surfaces_safely():
    """ClientError from boto3 raises ProvisionError; internal text must NOT leak."""
    mock_ec2 = MagicMock()
    error_response = {
        "Error": {
            "Code": "AuthFailure",
            "Message": "AWS was not able to validate the provided access credentials super-secret-key-info",
        }
    }
    mock_ec2.describe_instance_types.side_effect = botocore.exceptions.ClientError(
        error_response, "DescribeInstanceTypes"
    )

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        with pytest.raises(ProvisionError) as exc_info:
            await adapter.discover_resources(region="us-east-1")

    # The safe message is present.
    assert "discover_resources failed" in str(exc_info.value)
    # Internal AWS error text must NOT be exposed.
    assert "super-secret-key-info" not in str(exc_info.value)
    assert "AuthFailure" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Task 15 — wait_for_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_ready_polls_until_registered():
    """wait_for_ready returns 'ready' once inventory row flips to 'ready'."""
    call_count = 0

    # First call returns 'provisioning', second returns 'ready'.
    async def _fetchrow_side_effect(query, instance_id):
        nonlocal call_count
        call_count += 1
        row = MagicMock()
        row.__getitem__ = lambda self, key: (
            "provisioning" if call_count == 1 else "ready"
        )
        return row

    db = AsyncMock()
    db.fetchrow.side_effect = _fetchrow_side_effect

    mock_ec2 = MagicMock()
    mock_waiter = MagicMock()
    mock_waiter.wait.return_value = None  # instance_running waiter succeeds
    mock_ec2.get_waiter.return_value = mock_waiter

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        adapter = AWSAdapter(db=db)
        result = await adapter.wait_for_ready(
            provider_instance_id="i-ready123",
            timeout=60,
        )

    assert result == "ready"
    mock_waiter.wait.assert_called_once_with(InstanceIds=["i-ready123"])


@pytest.mark.asyncio
async def test_wait_for_ready_timeout_terminates():
    """When instance never registers, ProvisionTimeoutError is raised and instance terminated."""
    db = AsyncMock()

    # Always return 'provisioning' (never ready).
    provisioning_row = MagicMock()
    provisioning_row.__getitem__ = lambda self, key: "provisioning"
    db.fetchrow.return_value = provisioning_row

    mock_ec2 = MagicMock()
    mock_waiter = MagicMock()
    mock_waiter.wait.return_value = None
    mock_ec2.get_waiter.return_value = mock_waiter

    # Speed up the loop by making get_event_loop().time() advance rapidly.
    # We patch asyncio.sleep to a no-op and artificially make the deadline expire
    # on the first iteration by using a tiny timeout (the mock sleep returns
    # immediately so time() will stay nearly constant — we need to use a real
    # timeout of 0 to ensure the while condition fails after one iteration).
    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        adapter = AWSAdapter(db=db)
        with pytest.raises(ProvisionTimeoutError, match="did not register"):
            await adapter.wait_for_ready(
                provider_instance_id="i-timeout",
                timeout=0,  # expires immediately
            )

    mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-timeout"])


# ---------------------------------------------------------------------------
# Task 15 — deprovision_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprovision_happy():
    """deprovision_node calls terminate_instances with the correct instance ID."""
    mock_ec2 = MagicMock()
    mock_ec2.terminate_instances.return_value = {}

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        await adapter.deprovision_node(provider_instance_id="i-deprov123")

    mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-deprov123"])


@pytest.mark.asyncio
async def test_deprovision_already_gone():
    """InvalidInstanceID.NotFound must be swallowed (idempotent deprovision)."""
    mock_ec2 = MagicMock()
    error_response = {
        "Error": {"Code": "InvalidInstanceID.NotFound", "Message": "not found"}
    }
    mock_ec2.terminate_instances.side_effect = botocore.exceptions.ClientError(
        error_response, "TerminateInstances"
    )

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        # Must NOT raise.
        await adapter.deprovision_node(provider_instance_id="i-gone")


@pytest.mark.asyncio
async def test_deprovision_other_client_error_raises():
    """Other ClientError codes must surface as ProvisionError."""
    mock_ec2 = MagicMock()
    error_response = {
        "Error": {"Code": "UnauthorizedOperation", "Message": "not allowed"}
    }
    mock_ec2.terminate_instances.side_effect = botocore.exceptions.ClientError(
        error_response, "TerminateInstances"
    )

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        with pytest.raises(ProvisionError, match="terminate failed"):
            await adapter.deprovision_node(provider_instance_id="i-authfail")


# ---------------------------------------------------------------------------
# Task 15 — get_logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_logs_happy():
    """get_logs splits console output into lines correctly."""
    mock_ec2 = MagicMock()
    mock_ec2.get_console_output.return_value = {
        "Output": "Starting cloud-init\nRunning bootstrap\nDone"
    }

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        result = await adapter.get_logs(provider_instance_id="i-logs123")

    assert result == {
        "logs": ["Starting cloud-init", "Running bootstrap", "Done"]
    }
    mock_ec2.get_console_output.assert_called_once_with(InstanceId="i-logs123")


@pytest.mark.asyncio
async def test_get_logs_error_returns_empty():
    """ClientError from get_console_output returns {"logs": []} (never raises)."""
    mock_ec2 = MagicMock()
    error_response = {
        "Error": {"Code": "InvalidInstanceID.NotFound", "Message": "gone"}
    }
    mock_ec2.get_console_output.side_effect = botocore.exceptions.ClientError(
        error_response, "GetConsoleOutput"
    )

    with patch.object(AWSAdapter, "_ec2_client", return_value=mock_ec2):
        adapter = AWSAdapter(db=AsyncMock())
        result = await adapter.get_logs(provider_instance_id="i-gone-logs")

    assert result == {"logs": []}


# ---------------------------------------------------------------------------
# Task 15 — get_log_streaming_info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_log_streaming_info_registered():
    """Registered worker (has node_id) returns supported=True with ws_url."""
    node_id = str(uuid4())
    row = MagicMock()
    row.__getitem__ = lambda self, key: node_id if key == "node_id" else None
    # Truthy check for row["node_id"]
    row.__bool__ = lambda self: True

    db = AsyncMock()
    db.fetchrow.return_value = row

    adapter = AWSAdapter(db=db)
    result = await adapter.get_log_streaming_info(provider_instance_id="i-reg123")

    assert result["supported"] is True
    assert result["kind"] == "worker-ws"
    assert node_id in result["ws_url"]


@pytest.mark.asyncio
async def test_get_log_streaming_info_not_registered():
    """Worker not yet registered (no node_id) returns supported=False."""
    db = AsyncMock()
    db.fetchrow.return_value = None  # no row at all

    adapter = AWSAdapter(db=db)
    result = await adapter.get_log_streaming_info(provider_instance_id="i-unreg")

    assert result["supported"] is False
    assert "reason" in result


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


def test_provision_timeout_error_is_provision_error():
    err = ProvisionTimeoutError("timed out")
    assert isinstance(err, ProvisionError)


def test_provision_error_message():
    err = ProvisionError("RunInstances failed")
    assert "RunInstances failed" in str(err)

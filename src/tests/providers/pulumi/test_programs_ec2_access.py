"""What the EC2 launch program lets reach the instance.

Runs the real program under Pulumi's test mocks, so the assertions are on the
arguments the AWS resources are created with.
"""

from __future__ import annotations

import asyncio

import pulumi
import pytest

from providers.pulumi import programs


class _Mocks(pulumi.runtime.Mocks):
    def __init__(self):
        self.resources: dict[str, dict] = {}

    def new_resource(self, args):
        self.resources[args.typ] = args.inputs
        return [args.name + "_id", args.inputs]

    def call(self, args):
        return {}


@pytest.fixture(autouse=True)
def _event_loop():
    """Pulumi's mocks run on the thread's current loop; an earlier asyncio test
    leaves none, which Python 3.12 does not create on demand."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    asyncio.set_event_loop(None)
    loop.close()


def _resources(**over) -> dict[str, dict]:
    mocks = _Mocks()
    pulumi.runtime.set_mocks(mocks, preview=False)
    kwargs = dict(
        pool_id="p1", org_id="o1", bootstrap_id="b1", instance_type="g6.xlarge",
        region="us-east-1", ami_id="ami-1", subnet_id=None, security_group_ids=None,
        iam_instance_profile=None, root_volume_gb=50, user_data="#!/bin/sh",
    )
    program = programs.build_ec2_program(**{**kwargs, **over})

    @pulumi.runtime.test
    def _run():
        program()
        return pulumi.Output.from_input(None)

    _run()
    return mocks.resources


_INSTANCE = "aws:ec2/instance:Instance"
_SG = "aws:ec2/securityGroup:SecurityGroup"


def _ports(resources) -> list[int]:
    return sorted(int(rule["fromPort"]) for rule in resources[_SG]["ingress"])


@pytest.mark.parametrize("security_group_ids", [None, ["sg-0123456789abcdef0"]])
def test_the_instance_requires_imdsv2_with_one_hop(security_group_ids):
    """Without a session token, code on the box (or a bridge-networked engine
    container, one hop further) can not read the instance role's keys."""
    options = _resources(security_group_ids=security_group_ids)[_INSTANCE]["metadataOptions"]
    assert options["httpTokens"] == "required"
    assert options["httpPutResponseHopLimit"] == 1
    assert options["httpEndpoint"] == "enabled"


def test_ssh_stays_closed_without_operator_keys():
    assert _ports(_resources()) == [8080]


def test_ssh_opens_when_operator_keys_are_installed():
    assert _ports(_resources(ssh_ingress=True)) == [22, 8080]


def test_an_operator_security_group_is_used_as_given():
    resources = _resources(security_group_ids=["sg-0123456789abcdef0"], ssh_ingress=True)
    assert _SG not in resources
    assert resources[_INSTANCE]["vpcSecurityGroupIds"] == ["sg-0123456789abcdef0"]


@pytest.mark.parametrize("flag", [True, False])
def test_build_program_passes_the_ssh_flag_through(monkeypatch, flag):
    captured = {}
    monkeypatch.setattr(programs, "build_ec2_program",
                        lambda **kw: captured.update(kw) or (lambda: None))
    programs.build_program(spec={"provider": "aws", "ssh_ingress": flag}, stack_outputs={})
    assert captured["ssh_ingress"] is flag

"""build_program must size the EC2 root volume from the preflight-corrected
value (stack_outputs) before the raw spec, so a baked engine AMI larger than
spec.root_volume_gb still launches.

The pulumi program closure can't be introspected without a pulumi runtime, so
we capture the kwargs build_program forwards to build_ec2_program.
"""
from __future__ import annotations

from providers.pulumi import programs


def _capture(monkeypatch):
    captured = {}
    def _fake_build_ec2_program(**kwargs):
        captured.update(kwargs)
        return lambda: None
    monkeypatch.setattr(programs, "build_ec2_program", _fake_build_ec2_program)
    return captured


def _spec(**over):
    s = {"provider": "aws", "ami_id": "ami-baked", "pool_id": "p1",
         "org_id": "o1", "instance_type": "g6.xlarge", "region": "us-east-1"}
    s.update(over)
    return s


def test_stack_outputs_root_volume_wins_over_spec(monkeypatch):
    cap = _capture(monkeypatch)
    programs.build_program(spec=_spec(root_volume_gb=100),
                           stack_outputs={"ami_id": "ami-baked", "root_volume_gb": 130})
    assert cap["root_volume_gb"] == 130


def test_spec_root_volume_used_when_no_stack_output(monkeypatch):
    cap = _capture(monkeypatch)
    programs.build_program(spec=_spec(root_volume_gb=100), stack_outputs={})
    assert cap["root_volume_gb"] == 100


def test_root_volume_falls_back_to_default(monkeypatch):
    cap = _capture(monkeypatch)
    programs.build_program(spec=_spec(), stack_outputs={})
    assert cap["root_volume_gb"] == 50


def test_zero_stack_output_falls_through_to_spec(monkeypatch):
    # A 0/None stack output is falsy and must not shrink the spec value.
    cap = _capture(monkeypatch)
    programs.build_program(spec=_spec(root_volume_gb=120),
                           stack_outputs={"root_volume_gb": 0})
    assert cap["root_volume_gb"] == 120

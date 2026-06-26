"""Tests for latest_dlami_ami SSM lookup."""
import time
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from providers.pulumi import ami


def _fresh_cache():
    ami._DLAMI_CACHE.clear()


def test_latest_dlami_ami_returns_value():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-deadbeef"}}
    with patch("boto3.client", return_value=mock_ssm):
        out = ami.latest_dlami_ami("us-east-1")
    assert out == "ami-deadbeef"


def test_latest_dlami_ami_is_cached_per_region():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-abc"}}
    with patch("boto3.client", return_value=mock_ssm):
        ami.latest_dlami_ami("us-east-1")
        ami.latest_dlami_ami("us-east-1")
    assert mock_ssm.get_parameter.call_count == 1


def test_latest_dlami_ami_different_regions_independent():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "ami-east"}},
        {"Parameter": {"Value": "ami-west"}},
    ]
    with patch("boto3.client", return_value=mock_ssm):
        e = ami.latest_dlami_ami("us-east-1")
        w = ami.latest_dlami_ami("us-west-2")
    assert e == "ami-east"
    assert w == "ami-west"
    assert mock_ssm.get_parameter.call_count == 2


def test_latest_dlami_ami_cache_expires():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "ami-1"}}
    with patch("boto3.client", return_value=mock_ssm):
        ami.latest_dlami_ami("us-east-1")
        # Manually expire the cache.
        ami._DLAMI_CACHE[f"us-east-1::{ami._DLAMI_PARAMETER}"] = ("ami-1", time.time() - ami._DLAMI_TTL_S - 1)
        ami.latest_dlami_ami("us-east-1")
    assert mock_ssm.get_parameter.call_count == 2


def test_latest_dlami_ami_boto_error_raises():
    _fresh_cache()
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "ParameterNotFound", "Message": "x"}}, "GetParameter",
    )
    with patch("boto3.client", return_value=mock_ssm):
        with pytest.raises(ami.AMILookupError):
            ami.latest_dlami_ami("us-east-1")


from providers.pulumi import ami as ami_mod


class _FakeEC2:
    def __init__(self, images):
        self._images = images
        self.calls = []

    def describe_images(self, **kwargs):
        self.calls.append(kwargs)
        return {"Images": self._images}


def test_find_engine_ami_newest_wins(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    fake = _FakeEC2([
        {"ImageId": "ami-old", "CreationDate": "2026-06-01T00:00:00.000Z"},
        {"ImageId": "ami-new", "CreationDate": "2026-06-08T00:00:00.000Z"},
    ])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: fake)
    got = ami_mod.find_engine_ami("us-east-1", aws_access_key_id="k", aws_secret_access_key="s")
    assert got == "ami-new"
    assert fake.calls[0]["Owners"] == ["self"]
    assert any(f["Name"] == "tag:inferia:engine-cache" for f in fake.calls[0]["Filters"])


def test_find_engine_ami_none_when_empty(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    fake = _FakeEC2([])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: fake)
    assert ami_mod.find_engine_ami("us-east-1", aws_access_key_id="k", aws_secret_access_key="s") is None


def test_find_engine_ami_cached(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    calls = {"n": 0}
    def _client(region, **kw):
        calls["n"] += 1
        return _FakeEC2([{"ImageId": "ami-x", "CreationDate": "2026-06-08T00:00:00.000Z"}])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", _client)
    a = ami_mod.find_engine_ami("us-east-1", aws_access_key_id="k", aws_secret_access_key="s")
    b = ami_mod.find_engine_ami("us-east-1", aws_access_key_id="k", aws_secret_access_key="s")
    assert a == b == "ami-x"
    assert calls["n"] == 1  # second call served from TTL cache


def test_find_engine_ami_describe_error_returns_none(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    class _Boom:
        def describe_images(self, **kw):
            raise RuntimeError("AccessDenied")
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: _Boom())
    assert ami_mod.find_engine_ami("us-east-1", aws_access_key_id="k", aws_secret_access_key="s") is None


def test_resolve_ami_prefers_engine_for_gpu(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    monkeypatch.setattr(ami_mod, "find_engine_ami", lambda region, **kw: "ami-engine")
    monkeypatch.setattr(ami_mod, "latest_dlami_ami", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call DLAMI")))
    assert ami_mod.resolve_ami(region="us-east-1", instance_class="normal_gpu", creds=None) == "ami-engine"


def test_resolve_ami_falls_back_to_dlami_for_gpu(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    monkeypatch.setattr(ami_mod, "find_engine_ami", lambda region, **kw: None)
    monkeypatch.setattr(ami_mod, "latest_dlami_ami", lambda *a, **k: "ami-dlami")
    assert ami_mod.resolve_ami(region="us-east-1", instance_class="heavy_gpu", creds=None) == "ami-dlami"


def test_resolve_ami_cpu_never_uses_engine(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    monkeypatch.setattr(ami_mod, "find_engine_ami", lambda region, **kw: (_ for _ in ()).throw(AssertionError("CPU must not consult engine AMI")))
    monkeypatch.setattr(ami_mod, "latest_dlami_ami", lambda *a, **k: "ami-ubuntu")
    assert ami_mod.resolve_ami(region="us-east-1", instance_class="cpu", creds=None) == "ami-ubuntu"


def test_find_engine_ami_missing_creation_date(monkeypatch):
    ami_mod._ENGINE_AMI_CACHE.clear()
    fake = _FakeEC2([
        {"ImageId": "ami-nodate"},  # no CreationDate -> sorts as "" (oldest)
        {"ImageId": "ami-dated", "CreationDate": "2026-06-08T00:00:00.000Z"},
    ])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: fake)
    assert ami_mod.find_engine_ami("us-east-1", aws_access_key_id="k", aws_secret_access_key="s") == "ami-dated"


def test_engine_ec2_client_forwards_session_token(monkeypatch):
    import boto3
    captured = {}
    monkeypatch.setattr(boto3, "client", lambda svc, **kw: captured.update(kw) or "client")
    ami_mod._engine_ec2_client("us-east-1", aws_access_key_id="k", aws_secret_access_key="s", aws_session_token="tok")
    assert captured.get("aws_session_token") == "tok"
    captured.clear()
    ami_mod._engine_ec2_client("us-east-1", aws_access_key_id="k", aws_secret_access_key="s")
    assert "aws_session_token" not in captured


# --- ami_root_volume_gb: floor the launch root volume at the AMI snapshot --- #
class _Creds:
    def __init__(self, k="ak", s="sk", t=None):
        self.access_key_id = k
        self.secret_access_key = s
        self.session_token = t


def _img(root_name="/dev/sda1", mappings=None):
    return {"RootDeviceName": root_name, "BlockDeviceMappings": mappings or []}


def test_ami_root_volume_returns_root_device_size(monkeypatch):
    fake = _FakeEC2([_img("/dev/sda1", [
        {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 130, "SnapshotId": "snap-a"}},
        {"DeviceName": "/dev/sdb", "Ebs": {"VolumeSize": 8}},  # secondary, ignored
    ])])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: fake)
    assert ami_mod.ami_root_volume_gb("us-east-1", "ami-baked", creds=_Creds()) == 130
    assert fake.calls[0]["ImageIds"] == ["ami-baked"]


def test_ami_root_volume_falls_back_to_max_when_root_unmatched(monkeypatch):
    # RootDeviceName doesn't match any mapping -> use the largest EBS size so
    # the launch volume is never smaller than any snapshot.
    fake = _FakeEC2([_img("/dev/xvda", [
        {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 120}},
        {"DeviceName": "/dev/sdb", "Ebs": {"VolumeSize": 200}},
    ])])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: fake)
    assert ami_mod.ami_root_volume_gb("us-east-1", "ami-x", creds=_Creds()) == 200


def test_ami_root_volume_none_when_no_images(monkeypatch):
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: _FakeEC2([]))
    assert ami_mod.ami_root_volume_gb("us-east-1", "ami-missing", creds=_Creds()) is None


def test_ami_root_volume_none_when_no_ebs_mappings(monkeypatch):
    # ephemeral-only / no VolumeSize mappings -> None (caller keeps requested).
    fake = _FakeEC2([_img("/dev/sda1", [
        {"DeviceName": "/dev/sdc", "VirtualName": "ephemeral0"},
        {"DeviceName": "/dev/sda1", "Ebs": {}},
    ])])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: fake)
    assert ami_mod.ami_root_volume_gb("us-east-1", "ami-y", creds=_Creds()) is None


def test_ami_root_volume_empty_ami_id_short_circuits(monkeypatch):
    monkeypatch.setattr(ami_mod, "_engine_ec2_client",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no client for empty ami")))
    assert ami_mod.ami_root_volume_gb("us-east-1", "", creds=_Creds()) is None


def test_ami_root_volume_describe_error_returns_none(monkeypatch):
    class _Boom:
        def describe_images(self, **kw):
            raise RuntimeError("AccessDenied")
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", lambda region, **kw: _Boom())
    assert ami_mod.ami_root_volume_gb("us-east-1", "ami-z", creds=_Creds()) is None


def test_ami_root_volume_threads_creds(monkeypatch):
    captured = {}
    def _client(region, **kw):
        captured.update(kw)
        return _FakeEC2([_img("/dev/sda1", [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 64}}])])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", _client)
    out = ami_mod.ami_root_volume_gb("us-west-2", "ami-c", creds=_Creds("AK", "SK", "TOK"))
    assert out == 64
    assert captured["aws_access_key_id"] == "AK"
    assert captured["aws_secret_access_key"] == "SK"
    assert captured["aws_session_token"] == "TOK"


def test_ami_root_volume_no_creds_uses_default_chain(monkeypatch):
    captured = {}
    def _client(region, **kw):
        captured.update(kw)
        return _FakeEC2([_img("/dev/sda1", [{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 75}}])])
    monkeypatch.setattr(ami_mod, "_engine_ec2_client", _client)
    assert ami_mod.ami_root_volume_gb("us-east-1", "ami-d", creds=None) == 75
    assert captured.get("aws_access_key_id") is None

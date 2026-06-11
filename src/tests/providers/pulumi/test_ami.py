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

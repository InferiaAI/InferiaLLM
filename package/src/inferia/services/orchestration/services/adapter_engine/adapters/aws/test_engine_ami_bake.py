import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws import (
    engine_ami_bake as bake,
)

AWS_ENV = {"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"}


def test_build_bake_script_installs_toolkit_unconditionally():
    script = bake._build_bake_script(
        vllm_image="docker.io/vllm/vllm-openai:v0.22.1",
        worker_image="ghcr.io/inferiaai/inferia-worker:0.2.5",
    )
    assert "lspci" not in script
    assert "command -v nvidia-ctk" not in script
    assert "nvidia-container-toolkit" in script
    assert "nvidia-ctk runtime configure --runtime=docker" in script
    assert "docker.io/vllm/vllm-openai:v0.22.1" in script
    assert "ghcr.io/inferiaai/inferia-worker:0.2.5" in script


def test_build_bake_script_omits_worker_pull_when_none():
    script = bake._build_bake_script(vllm_image="img:tag", worker_image=None)
    assert "docker pull img:tag" in script
    assert script.count("docker pull") == 1


def test_bake_requires_instance_profile():
    with pytest.raises(bake.BakeError):
        bake.bake_engine_ami(region="us-east-1", aws_env=AWS_ENV, ssm_instance_profile=None)


def test_bake_requires_aws_env():
    with pytest.raises(bake.BakeError):
        bake.bake_engine_ami(region="us-east-1", aws_env=None, ssm_instance_profile="p")


class _FakeEC2:
    def __init__(self):
        self.terminated = []
        self.created_image = None
        self.tagged = None
        self.waiters = []

    def run_instances(self, **kw):
        self.run_kwargs = kw
        return {"Instances": [{"InstanceId": "i-build"}]}

    def stop_instances(self, **kw):
        self.stopped = kw["InstanceIds"]

    def create_image(self, **kw):
        self.created_image = kw
        return {"ImageId": "ami-baked"}

    def create_tags(self, **kw):
        self.tagged = kw

    def terminate_instances(self, **kw):
        self.terminated.extend(kw["InstanceIds"])

    def get_waiter(self, name):
        self.waiters.append(name)
        return _NoopWaiter()


class _NoopWaiter:
    def wait(self, **kw):
        return None


class _FakeSSM:
    def __init__(self, status="Success"):
        self._status = status
        self.online = True

    def describe_instance_information(self, **kw):
        return {"InstanceInformationList": [{"InstanceId": "i-build"}]} if self.online else {"InstanceInformationList": []}

    def send_command(self, **kw):
        self.command = kw
        return {"Command": {"CommandId": "cmd-1"}}

    def get_command_invocation(self, **kw):
        return {"Status": self._status, "StandardErrorContent": "boom"}


def _patch(monkeypatch, ec2, ssm):
    monkeypatch.setattr(bake, "_ec2_client", lambda region, **kw: ec2)
    monkeypatch.setattr(bake, "_ssm_client", lambda region, **kw: ssm)
    monkeypatch.setattr(bake, "latest_dlami_ami", lambda *a, **k: "ami-dlami")


def test_bake_happy_path(monkeypatch):
    ec2, ssm = _FakeEC2(), _FakeSSM("Success")
    _patch(monkeypatch, ec2, ssm)
    res = bake.bake_engine_ami(region="us-east-1", aws_env=AWS_ENV, ssm_instance_profile="inferia-bake-ssm")
    assert res.ami_id == "ami-baked"
    assert res.region == "us-east-1"
    assert ec2.terminated == ["i-build"]
    tag_keys = {t["Key"] for t in ec2.tagged["Tags"]}
    assert "inferia:engine-cache" in tag_keys and "inferia:vllm-tag" in tag_keys
    assert "image_available" in ec2.waiters


def test_bake_ssm_failure_terminates_builder(monkeypatch):
    ec2, ssm = _FakeEC2(), _FakeSSM("Failed")
    _patch(monkeypatch, ec2, ssm)
    with pytest.raises(bake.BakeError) as ei:
        bake.bake_engine_ami(region="us-east-1", aws_env=AWS_ENV, ssm_instance_profile="inferia-bake-ssm")
    assert "boom" in str(ei.value)
    assert ec2.terminated == ["i-build"]
    assert ec2.created_image is None


def test_bake_create_image_failure_terminates_builder(monkeypatch):
    ec2, ssm = _FakeEC2(), _FakeSSM("Success")
    def boom(**kw):
        raise RuntimeError("CreateImage denied")
    ec2.create_image = boom
    _patch(monkeypatch, ec2, ssm)
    with pytest.raises(bake.BakeError):
        bake.bake_engine_ami(region="us-east-1", aws_env=AWS_ENV, ssm_instance_profile="inferia-bake-ssm")
    assert ec2.terminated == ["i-build"]


def test_build_bake_script_rejects_injection():
    with pytest.raises(bake.BakeError):
        bake._build_bake_script(vllm_image="img:v1; rm -rf /tmp", worker_image=None)


def test_build_bake_script_rejects_injection_in_worker():
    with pytest.raises(bake.BakeError):
        bake._build_bake_script(vllm_image="img:tag", worker_image="w:1 && curl evil")


def test_bake_injection_tag_fails_before_launch(monkeypatch):
    ec2, ssm = _FakeEC2(), _FakeSSM("Success")
    _patch(monkeypatch, ec2, ssm)
    with pytest.raises(bake.BakeError):
        bake.bake_engine_ami(
            region="us-east-1", aws_env=AWS_ENV,
            ssm_instance_profile="p", vllm_tag="v1; rm -rf /",
        )
    # Validation happens before run_instances, so no builder was launched.
    assert ec2.terminated == []


def test_bake_ssm_never_online_terminates_builder(monkeypatch):
    ec2, ssm = _FakeEC2(), _FakeSSM("Success")
    ssm.online = False
    _patch(monkeypatch, ec2, ssm)
    monkeypatch.setattr(bake, "_SSM_ONLINE_TIMEOUT_S", -1)  # deadline already past
    with pytest.raises(bake.BakeError) as ei:
        bake.bake_engine_ami(region="us-east-1", aws_env=AWS_ENV, ssm_instance_profile="p")
    assert "SSM-managed" in str(ei.value)
    assert ec2.terminated == ["i-build"]  # finally still terminates


def test_wait_ssm_times_out(monkeypatch):
    monkeypatch.setattr(bake, "_SSM_CMD_TIMEOUT_S", -61)  # deadline already past
    ssm = _FakeSSM("Pending")
    out = bake._wait_ssm(ssm, "cmd-1", "i-build")
    assert out["Status"] == "TimedOut"


def test_wait_ssm_retries_on_transient_error(monkeypatch):
    monkeypatch.setattr(bake.time, "sleep", lambda *a, **k: None)
    calls = {"n": 0}
    class _FlakySSM:
        def get_command_invocation(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("InvocationDoesNotExist")
            return {"Status": "Success"}
    out = bake._wait_ssm(_FlakySSM(), "cmd-1", "i-build")
    assert out["Status"] == "Success" and calls["n"] == 2

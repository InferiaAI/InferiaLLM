"""Bake a custom EC2 AMI with the vLLM engine image (and worker image)
pre-pulled + extracted, so cold GPU nodes skip the ~15-20 min pull+extract.

Sync boto3 (mirrors aws_orphan_sweep.py): client seams are monkeypatchable;
``aws_env`` is resolved by the async caller and passed in. The builder is a
CPU instance (avoids the G-vCPU quota; docker pull+extract needs no GPU); the
resulting AMI is launchable on x86_64 GPU instances. See
docs/specs/2026-06-09-vllm-deploy-robustness-design.md.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from inferia.services.orchestration.services.adapter_engine.adapters.pulumi.ami import (
    latest_dlami_ami,
)
from inferia.services.orchestration.services.adapter_engine.aws_orphan_sweep import (
    _creds_from_aws_env,
)

logger = logging.getLogger(__name__)

_DEFAULT_VLLM_TAG = "v0.22.1"
_DEFAULT_INSTANCE_TYPE = "t3.xlarge"  # CPU; standard quota; x86_64
_DEFAULT_ROOT_GB = 100                # matches the GPU launch root_volume_gb
_BUILDER_TAG = "inferia:engine-ami-builder"
_ENGINE_CACHE_TAG = "inferia:engine-cache"
_SSM_ONLINE_TIMEOUT_S = 300
_SSM_CMD_TIMEOUT_S = 1800

# Valid OCI image-reference characters (registry/host . : / , name _ - , tag : ,
# digest @). Anything else (whitespace, ; | & $ ` newlines) is a shell-injection
# vector because the ref is interpolated into the SSM shell script.
_IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9_./:@-]+$")


class BakeError(RuntimeError):
    """Any failure in the bake pipeline, string-classified for the caller."""


def _validate_image_ref(ref: str) -> str:
    """Reject image refs that could break out of the `docker pull <ref>` shell
    line in the SSM bake script. Mirrors bootstrap_builder's input-hardening."""
    if not ref or "\x00" in ref or len(ref) > 512 or not _IMAGE_REF_RE.match(ref):
        raise BakeError(f"invalid/unsafe image reference: {ref!r}")
    return ref


@dataclass
class BakeResult:
    ami_id: str
    region: str
    vllm_tag: str
    base_dlami: str


def _ec2_client(region: str, *, creds: dict):
    import boto3
    return boto3.client("ec2", region_name=region, **creds)


def _ssm_client(region: str, *, creds: dict):
    import boto3
    return boto3.client("ssm", region_name=region, **creds)


def _build_bake_script(*, vllm_image: str, worker_image: Optional[str]) -> str:
    """Shell script run on the builder via SSM. Installs docker + the
    nvidia-container-toolkit UNCONDITIONALLY (the CPU builder has no NVIDIA
    device, so an lspci-guarded install would skip and the AMI would lack the
    runtime shim that GPU nodes need), then pulls the images so they are baked
    into the image store."""
    _validate_image_ref(vllm_image)
    if worker_image:
        _validate_image_ref(worker_image)
    lines = [
        "set -euxo pipefail",
        "export DEBIAN_FRONTEND=noninteractive",
        "if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi",
        "distribution=$(. /etc/os-release; echo $ID$VERSION_ID)",
        "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
        "curl -fsSL https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list",
        "apt-get -o DPkg::Lock::Timeout=600 update",
        "apt-get -o DPkg::Lock::Timeout=600 install -y nvidia-container-toolkit",
        "nvidia-ctk runtime configure --runtime=docker",
        "systemctl restart docker",
        f"docker pull {vllm_image}",
    ]
    if worker_image:
        lines.append(f"docker pull {worker_image}")
    lines.append("docker image ls")
    return "\n".join(lines)


def bake_engine_ami(
    *,
    region: str,
    aws_env: Optional[dict],
    vllm_tag: str = _DEFAULT_VLLM_TAG,
    worker_image_ref: Optional[str] = None,
    instance_type: str = _DEFAULT_INSTANCE_TYPE,
    root_volume_gb: int = _DEFAULT_ROOT_GB,
    ssm_instance_profile: Optional[str] = None,
) -> BakeResult:
    if not aws_env:
        raise BakeError("no AWS credentials resolved for the bake")
    if not ssm_instance_profile:
        raise BakeError(
            "ssm_instance_profile is required (builder must be SSM-managed); "
            "configure an instance profile with AmazonSSMManagedInstanceCore"
        )
    creds = _creds_from_aws_env(aws_env)
    dlami = latest_dlami_ami(
        region,
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
    )
    vllm_image = f"docker.io/vllm/vllm-openai:{vllm_tag}"
    script = _build_bake_script(vllm_image=vllm_image, worker_image=worker_image_ref)

    ec2 = _ec2_client(region, creds=creds)
    ssm = _ssm_client(region, creds=creds)
    instance_id: Optional[str] = None
    try:
        run = ec2.run_instances(
            ImageId=dlami,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            IamInstanceProfile={"Name": ssm_instance_profile},
            BlockDeviceMappings=[{
                "DeviceName": "/dev/sda1",
                "Ebs": {"VolumeSize": int(root_volume_gb), "VolumeType": "gp3"},
            }],
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"inferia-engine-ami-builder-{vllm_tag}"},
                    {"Key": _BUILDER_TAG, "Value": "true"},
                ],
            }],
        )
        instance_id = run["Instances"][0]["InstanceId"]
        logger.info("engine_ami_bake: builder %s launching in %s", instance_id, region)

        ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

        deadline = time.time() + _SSM_ONLINE_TIMEOUT_S
        while time.time() < deadline:
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if info.get("InstanceInformationList"):
                break
            time.sleep(10)
        else:
            raise BakeError(f"builder {instance_id} never became SSM-managed")

        cmd = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [script]},
            TimeoutSeconds=_SSM_CMD_TIMEOUT_S,
        )
        command_id = cmd["Command"]["CommandId"]
        status = _wait_ssm(ssm, command_id, instance_id)
        if status["Status"] != "Success":
            raise BakeError(
                f"bake command status={status['Status']}: "
                f"{(status.get('StandardErrorContent') or '')[-500:]}"
            )

        ec2.stop_instances(InstanceIds=[instance_id])
        ec2.get_waiter("instance_stopped").wait(InstanceIds=[instance_id])

        img = ec2.create_image(
            InstanceId=instance_id,
            Name=f"inferia-engine-cache-{vllm_tag}-{instance_id}",
            Description=f"DLAMI {dlami} + vllm-openai:{vllm_tag} pre-pulled",
        )
        ami_id = img["ImageId"]
        ec2.create_tags(
            Resources=[ami_id],
            Tags=[
                {"Key": _ENGINE_CACHE_TAG, "Value": "true"},
                {"Key": "inferia:vllm-tag", "Value": vllm_tag},
                {"Key": "inferia:base-dlami", "Value": dlami},
                {"Key": "Name", "Value": f"inferia-engine-cache-{vllm_tag}"},
            ],
        )
        ec2.get_waiter("image_available").wait(ImageIds=[ami_id])
        logger.info("engine_ami_bake: baked %s in %s", ami_id, region)
        return BakeResult(ami_id=ami_id, region=region, vllm_tag=vllm_tag, base_dlami=dlami)
    except BakeError:
        raise
    except Exception as e:  # noqa: BLE001
        raise BakeError(f"{type(e).__name__}: {e}") from e
    finally:
        if instance_id:
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
                logger.info("engine_ami_bake: terminated builder %s", instance_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("engine_ami_bake: failed to terminate builder %s: %s", instance_id, e)


def _wait_ssm(ssm, command_id: str, instance_id: str) -> dict:
    deadline = time.time() + _SSM_CMD_TIMEOUT_S + 60
    terminal = {"Success", "Failed", "Cancelled", "TimedOut"}
    last = {"Status": "Pending"}
    while time.time() < deadline:
        try:
            last = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except Exception:  # noqa: BLE001 — invocation not registered yet
            time.sleep(5)
            continue
        if last.get("Status") in terminal:
            return last
        time.sleep(10)
    return {"Status": "TimedOut", "StandardErrorContent": "SSM command poll timed out"}


__all__ = ["bake_engine_ami", "BakeResult", "BakeError"]

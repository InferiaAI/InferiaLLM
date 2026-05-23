"""Render cloud-init user-data for an AWS EC2 worker bootstrap.

All interpolated values pass through shlex.quote. Inputs containing NUL or
exceeding 1024 chars are rejected up front.
"""
from __future__ import annotations

import shlex


class InvalidBootstrapInput(ValueError):
    """Raised when an input field is unsafe for shell interpolation."""


_MAX_FIELD_LEN = 1024


def _validate(name: str, value: str) -> str:
    if "\x00" in value:
        raise InvalidBootstrapInput(f"{name} contains NUL")
    if len(value) > _MAX_FIELD_LEN:
        raise InvalidBootstrapInput(f"{name} > {_MAX_FIELD_LEN} chars")
    return value


_TEMPLATE = r"""#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/inferia-bootstrap.log) 2>&1

echo "[inferia-bootstrap] starting at $(date -Is)"

if ! command -v docker >/dev/null; then
  echo "[inferia-bootstrap] installing docker"
  curl -fsSL https://get.docker.com | sh
fi

if lspci 2>/dev/null | grep -qi nvidia && ! command -v nvidia-ctk >/dev/null; then
  echo "[inferia-bootstrap] installing nvidia-container-toolkit"
  distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update && apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

mkdir -p /var/lib/inferia-worker
docker pull {image_full}
docker rm -f inferia-worker 2>/dev/null || true

GPU_FLAG=""
if lspci 2>/dev/null | grep -qi nvidia; then GPU_FLAG="--gpus=all"; fi

docker run -d --name inferia-worker --restart=always $GPU_FLAG \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/inferia-worker:/var/lib/inferia-worker \
  --network host \
  -e BOOTSTRAP_TOKEN={bootstrap_token} \
  -e CONTROL_PLANE_URL={control_plane_url} \
  -e NODE_NAME={node_name} \
  -e POOL_ID={pool_id} \
  {image_full}

echo "[inferia-bootstrap] done at $(date -Is)"
"""


def build_user_data(
    *,
    bootstrap_token: str,
    control_plane_url: str,
    node_name: str,
    pool_id: str,
    image: str,
    image_tag: str,
) -> str:
    """Build a shell-safe cloud-init user-data script.

    Args:
        bootstrap_token: One-time token the worker uses to register with the
            control plane.
        control_plane_url: Base URL of the control plane.
        node_name: EC2 instance identifier (e.g. ``i-0abc123``).
        pool_id: UUID of the node pool this worker belongs to.
        image: Container image name (without tag).
        image_tag: Container image tag.

    Returns:
        A ``#!/bin/bash`` script suitable for use as EC2 user-data.

    Raises:
        InvalidBootstrapInput: If any field contains a NUL byte or exceeds
            1024 characters.
    """
    bootstrap_token = _validate("bootstrap_token", bootstrap_token)
    control_plane_url = _validate("control_plane_url", control_plane_url)
    node_name = _validate("node_name", node_name)
    pool_id = _validate("pool_id", pool_id)
    image = _validate("image", image)
    image_tag = _validate("image_tag", image_tag)

    image_full = shlex.quote(f"{image}:{image_tag}")
    return _TEMPLATE.format(
        bootstrap_token=shlex.quote(bootstrap_token),
        control_plane_url=shlex.quote(control_plane_url),
        node_name=shlex.quote(node_name),
        pool_id=shlex.quote(pool_id),
        image_full=image_full,
    )

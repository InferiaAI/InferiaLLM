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

# zsh + SSH for ubuntu and root, when authorized_keys is supplied.
{ssh_block}

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

# Discover the EC2 public hostname so we can build WORKER_ADVERTISE_URL.
# IMDSv2 is required on modern AMIs; fall back to IMDSv1 if the token
# request fails (older AMIs / non-AWS clones).
IMDS_TOKEN=$(curl -fsS -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 300" http://169.254.169.254/latest/api/token 2>/dev/null || true)
if [ -n "$IMDS_TOKEN" ]; then
  PUBLIC_HOST=$(curl -fsS -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/public-hostname 2>/dev/null || true)
else
  PUBLIC_HOST=$(curl -fsS http://169.254.169.254/latest/meta-data/public-hostname 2>/dev/null || true)
fi
if [ -z "$PUBLIC_HOST" ]; then
  # No public DNS (instance without a public IP). Fall back to private IP.
  PUBLIC_HOST=$(hostname -I | awk '{{print $1}}')
fi
echo "[inferia-bootstrap] WORKER_ADVERTISE_URL host = $PUBLIC_HOST"

docker run -d --name inferia-worker --restart=always $GPU_FLAG \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/inferia-worker:/var/lib/inferia-worker \
  --network host \
  -e BOOTSTRAP_TOKEN={bootstrap_token} \
  -e CONTROL_PLANE_URL={control_plane_url} \
  -e NODE_NAME={node_name} \
  -e POOL_ID={pool_id} \
  -e WORKER_ADVERTISE_URL="http://$PUBLIC_HOST:8080" \
  -e INFERENCE_TOKEN={inference_token} \
  {image_full}

# --- inferia diagnostic block (cloud-init console output) ---------------------
# Reachability checks + worker container logs piped to the cloud-init console
# so an operator without SSH/SSM access can read them via ec2.get_console_output.
echo "[inferia-diag] DNS lookup of control plane:"
getent hosts $(echo {control_plane_url} | sed -E 's|^https?://||' | cut -d/ -f1 | cut -d: -f1) || true
echo "[inferia-diag] reachability probe:"
curl -sS --max-time 10 -o /dev/null -w 'HTTP %{{http_code}} in %{{time_total}}s\n' {control_plane_url}/health || true
echo "[inferia-diag] giving worker container 30s to start..."
sleep 30
echo "[inferia-diag] docker ps:"
docker ps --format '{{{{.Names}}}} {{{{.Status}}}}' || true
echo "[inferia-diag] inferia-worker logs (tail 80):"
docker logs --tail 80 inferia-worker 2>&1 || true
echo "[inferia-bootstrap] done at $(date -Is)"
"""


_SSH_BLOCK_NOOP = '# (no SSH authorized_keys configured; SSH disabled by default)\n'


def _build_ssh_block(ssh_authorized_keys: str) -> str:
    """Render the bash snippet that installs zsh + writes authorized_keys.

    ``ssh_authorized_keys`` is the raw authorized_keys file contents (one
    public key per line, blank lines / # comments allowed). When empty we
    return a no-op comment so cloud-init still parses cleanly.
    """
    keys = (ssh_authorized_keys or "").strip()
    if not keys:
        return _SSH_BLOCK_NOOP
    # Validate combined size: authorized_keys can grow if multiple keys
    # are listed, so the 1 KiB per-field cap doesn't apply here. AWS caps
    # the whole user-data at 16 KiB; we keep keys under 8 KiB.
    if "\x00" in keys:
        raise InvalidBootstrapInput("ssh_authorized_keys contains NUL")
    if len(keys) > 8192:
        raise InvalidBootstrapInput("ssh_authorized_keys > 8 KiB")
    quoted = shlex.quote(keys + "\n")
    return f"""\
echo "[inferia-bootstrap] installing zsh + bash"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zsh bash openssh-server

echo "[inferia-bootstrap] injecting authorized_keys for ubuntu + root"
SSH_KEYS={quoted}
install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
printf '%s' "$SSH_KEYS" > /home/ubuntu/.ssh/authorized_keys
chown ubuntu:ubuntu /home/ubuntu/.ssh/authorized_keys
chmod 600 /home/ubuntu/.ssh/authorized_keys
install -d -m 700 -o root -g root /root/.ssh
printf '%s' "$SSH_KEYS" > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Enable root SSH via public key (still no password login).
if grep -qE '^[#[:space:]]*PermitRootLogin' /etc/ssh/sshd_config; then
  sed -i 's|^[#[:space:]]*PermitRootLogin.*|PermitRootLogin prohibit-password|' /etc/ssh/sshd_config
else
  echo 'PermitRootLogin prohibit-password' >> /etc/ssh/sshd_config
fi
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
echo "[inferia-bootstrap] SSH configured: ubuntu + root accept the supplied key(s)"
"""


def build_user_data(
    *,
    bootstrap_token: str,
    control_plane_url: str,
    node_name: str,
    pool_id: str,
    image: str,
    image_tag: str,
    inference_token: str,
    ssh_authorized_keys: str = "",
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
        inference_token: Per-pool token the control plane uses to
            authenticate inbound CP→worker traffic. Without it the
            inferia-worker container exits with
            ``INFERENCE_TOKEN is required``.
        ssh_authorized_keys: Raw authorized_keys file contents (one
            public key per line). When non-empty the bootstrap installs
            zsh + bash, writes the keys for ``ubuntu`` and ``root``,
            and enables root SSH via key (no password login). Empty
            string skips the SSH setup entirely.

    Returns:
        A ``#!/bin/bash`` script suitable for use as EC2 user-data.

    Raises:
        InvalidBootstrapInput: If any field contains a NUL byte or exceeds
            its size cap.
    """
    bootstrap_token = _validate("bootstrap_token", bootstrap_token)
    control_plane_url = _validate("control_plane_url", control_plane_url)
    node_name = _validate("node_name", node_name)
    pool_id = _validate("pool_id", pool_id)
    image = _validate("image", image)
    image_tag = _validate("image_tag", image_tag)
    inference_token = _validate("inference_token", inference_token)

    image_full = shlex.quote(f"{image}:{image_tag}")
    return _TEMPLATE.format(
        bootstrap_token=shlex.quote(bootstrap_token),
        control_plane_url=shlex.quote(control_plane_url),
        node_name=shlex.quote(node_name),
        pool_id=shlex.quote(pool_id),
        image_full=image_full,
        inference_token=shlex.quote(inference_token),
        ssh_block=_build_ssh_block(ssh_authorized_keys),
    )

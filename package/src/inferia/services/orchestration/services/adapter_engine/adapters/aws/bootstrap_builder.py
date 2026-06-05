"""Render cloud-init user-data for an AWS EC2 worker bootstrap.

All interpolated values pass through shlex.quote. Inputs containing NUL or
exceeding 1024 chars are rejected up front.

The script branches on ``instance_class``:

- ``normal_gpu`` / ``heavy_gpu``: install nvidia-container-toolkit, pass
  ``--gpus all`` to ``docker run``, and advertise ``gpu_count`` to the
  worker via ``ALLOCATABLE_GPU_OVERRIDE`` so the worker reports its
  capacity without depending on ``nvidia-smi`` being present in the
  container image.
- ``cpu``: skip NVIDIA driver / runtime setup, omit ``--gpus``, and
  advertise ``ALLOCATABLE_GPU_OVERRIDE=0``.

Anything else raises ``ValueError`` so a malformed tier from the wizard
fails loud rather than producing a script that silently mismatches the
EC2 instance type.
"""
from __future__ import annotations

import shlex


class InvalidBootstrapInput(ValueError):
    """Raised when an input field is unsafe for shell interpolation."""


_MAX_FIELD_LEN = 1024

_VALID_INSTANCE_CLASSES = frozenset({"normal_gpu", "heavy_gpu", "cpu"})


def _validate(name: str, value: str) -> str:
    if "\x00" in value:
        raise InvalidBootstrapInput(f"{name} contains NUL")
    if len(value) > _MAX_FIELD_LEN:
        raise InvalidBootstrapInput(f"{name} > {_MAX_FIELD_LEN} chars")
    return value


# Bash block that installs the NVIDIA container toolkit. Rendered only on
# GPU tiers. Kept as a raw string so the outer .format() doesn't get
# confused by the embedded ${ID}${VERSION_ID} expansions; consumers of
# _TEMPLATE inject this verbatim via the {nvidia_block} slot.
_NVIDIA_INSTALL_BLOCK = r"""if lspci 2>/dev/null | grep -qi nvidia && ! command -v nvidia-ctk >/dev/null; then
  echo "[inferia-bootstrap] installing nvidia-container-toolkit"
  distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
    gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get ${APT_OPTS:-} update && apt-get ${APT_OPTS:-} install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi
"""

_NVIDIA_SKIP_BLOCK = "# CPU-only instance: skipping NVIDIA driver / runtime setup\n"

# On GPU tiers we always want --gpus all; on CPU tiers we want no GPU
# flag at all. We render the literal flag (not the lspci-probed runtime
# variable) so the user-data is deterministic w.r.t. the wizard tier.
_GPU_FLAG_LINE = 'GPU_FLAG="--gpus all"\n'
_CPU_FLAG_LINE = 'GPU_FLAG=""\n'

_TEMPLATE = r"""#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/inferia-bootstrap.log) 2>&1

echo "[inferia-bootstrap] starting at $(date -Is) (instance_class={instance_class}, gpu_count={gpu_count_literal})"

# The DLAMI auto-runs unattended-upgrades + apt-daily at boot, which hold the
# dpkg lock. Under `set -e` the FIRST apt-get below would race that lock, fail
# with "Could not get lock /var/lib/dpkg/lock-frontend", and abort the ENTIRE
# bootstrap -> the worker is never installed -> the node never registers ->
# the deployment hangs in 'bootstrapping' forever. Quiesce those units, wait
# for any in-flight run to release the lock, and tell apt itself to wait too
# (DPkg::Lock::Timeout) in case the lock is briefly re-acquired mid-run.
echo "[inferia-bootstrap] quiescing unattended-upgrades / apt-daily before install"
systemctl stop unattended-upgrades.service apt-daily.service apt-daily-upgrade.service apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
APT_OPTS="-o DPkg::Lock::Timeout=600"
for _i in $(seq 1 120); do
  fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock >/dev/null 2>&1 || break
  sleep 5
done

# zsh + SSH for ubuntu and root, when authorized_keys is supplied.
{ssh_block}

if ! command -v docker >/dev/null; then
  echo "[inferia-bootstrap] installing docker"
  curl -fsSL https://get.docker.com | sh
fi

{nvidia_block}
mkdir -p /var/lib/inferia-worker
docker pull {image_full}
docker rm -f inferia-worker 2>/dev/null || true

{gpu_flag_line}
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
  -e ALLOCATABLE_GPU_OVERRIDE={gpu_count_literal} \
  -e ALLOCATABLE_GPU_MODELS_OVERRIDE={gpu_models_override} \
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
DEBIAN_FRONTEND=noninteractive apt-get ${{APT_OPTS:-}} update -qq
DEBIAN_FRONTEND=noninteractive apt-get ${{APT_OPTS:-}} install -y -qq zsh bash openssh-server

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
    image: str = "",
    image_tag: str = "",
    inference_token: str,
    ssh_authorized_keys: str = "",
    instance_class: str = "normal_gpu",
    gpu_count: int = 1,
    worker_image: str | None = None,
) -> str:
    """Build a shell-safe cloud-init user-data script.

    Args:
        bootstrap_token: One-time token the worker uses to register with the
            control plane.
        control_plane_url: Base URL of the control plane.
        node_name: EC2 instance identifier (e.g. ``i-0abc123``).
        pool_id: UUID of the node pool this worker belongs to.
        image: Container image name (without tag). Mutually exclusive
            with ``worker_image``.
        image_tag: Container image tag. Mutually exclusive with
            ``worker_image``.
        inference_token: Per-pool token the control plane uses to
            authenticate inbound CP→worker traffic. Without it the
            inferia-worker container exits with
            ``INFERENCE_TOKEN is required``.
        ssh_authorized_keys: Raw authorized_keys file contents (one
            public key per line). When non-empty the bootstrap installs
            zsh + bash, writes the keys for ``ubuntu`` and ``root``,
            and enables root SSH via key (no password login). Empty
            string skips the SSH setup entirely.
        instance_class: EC2 wizard tier this worker is being provisioned
            into. Must be one of ``"normal_gpu"``, ``"heavy_gpu"``, or
            ``"cpu"``.

            * ``normal_gpu`` / ``heavy_gpu``: installs
              ``nvidia-container-toolkit``, passes ``--gpus all`` to
              ``docker run``, and advertises ``gpu_count`` to the worker
              via ``ALLOCATABLE_GPU_OVERRIDE``.
            * ``cpu``: skips NVIDIA driver / runtime install, omits the
              ``--gpus`` flag, and advertises ``ALLOCATABLE_GPU_OVERRIDE=0``.
        gpu_count: Number of GPUs the worker should advertise via
            ``ALLOCATABLE_GPU_OVERRIDE``. Should be ``0`` for the CPU
            tier and ``>=1`` for the GPU tiers (the value is not checked
            against the tier — the wizard / orchestrator is responsible
            for keeping them consistent).
        worker_image: Convenience alias for callers that already have
            ``"image:tag"`` joined into one string (matches the plan
            sketch's signature). If supplied it overrides ``image`` and
            ``image_tag``.

    Returns:
        A ``#!/bin/bash`` script suitable for use as EC2 user-data.

    Raises:
        InvalidBootstrapInput: If any field contains a NUL byte or exceeds
            its size cap.
        ValueError: If ``instance_class`` is not one of the supported
            tiers. We deliberately raise the plain ``ValueError`` (not
            ``InvalidBootstrapInput``) because a bad tier is a wiring
            mismatch with the wizard, not an injection attempt; the
            tests in ``test_bootstrap_builder.py`` rely on this
            distinction.
    """
    if instance_class not in _VALID_INSTANCE_CLASSES:
        raise ValueError(
            f"unknown instance_class: {instance_class!r} "
            f"(expected one of {sorted(_VALID_INSTANCE_CLASSES)})"
        )

    # T12 code review I-2: catch wiring bugs from T15/T23 at boot-script
    # generation time rather than at worker-registration time, where the
    # symptom is much further from the cause.
    if not isinstance(gpu_count, int) or isinstance(gpu_count, bool):
        raise ValueError(f"gpu_count must be a non-bool int; got {gpu_count!r}")
    if gpu_count < 0:
        raise ValueError(f"gpu_count must be >= 0; got {gpu_count}")
    if instance_class == "cpu" and gpu_count != 0:
        raise ValueError(
            f"instance_class='cpu' requires gpu_count=0; got {gpu_count}"
        )

    bootstrap_token = _validate("bootstrap_token", bootstrap_token)
    control_plane_url = _validate("control_plane_url", control_plane_url)
    node_name = _validate("node_name", node_name)
    pool_id = _validate("pool_id", pool_id)
    inference_token = _validate("inference_token", inference_token)

    # T12 code review I-4: enforce mutual exclusion the docstring claims.
    if worker_image is not None:
        if image or image_tag:
            raise ValueError(
                "worker_image is mutually exclusive with image / image_tag; "
                "pass one form or the other, not both"
            )
        worker_image = _validate("worker_image", worker_image)
        image_full = shlex.quote(worker_image)
    else:
        image = _validate("image", image)
        image_tag = _validate("image_tag", image_tag)
        image_full = shlex.quote(f"{image}:{image_tag}")

    is_gpu = instance_class != "cpu"
    nvidia_block = _NVIDIA_INSTALL_BLOCK if is_gpu else _NVIDIA_SKIP_BLOCK
    gpu_flag_line = _GPU_FLAG_LINE if is_gpu else _CPU_FLAG_LINE
    # ALLOCATABLE_GPU_MODELS_OVERRIDE is read by the worker's recipe
    # filter. Leave it empty on CPU tiers so the worker won't try to
    # match GPU-only recipes.
    gpu_models_override = "NVIDIA" if is_gpu else ""

    return _TEMPLATE.format(
        bootstrap_token=shlex.quote(bootstrap_token),
        control_plane_url=shlex.quote(control_plane_url),
        node_name=shlex.quote(node_name),
        pool_id=shlex.quote(pool_id),
        image_full=image_full,
        inference_token=shlex.quote(inference_token),
        ssh_block=_build_ssh_block(ssh_authorized_keys),
        instance_class=instance_class,
        gpu_count_literal=int(gpu_count),
        nvidia_block=nvidia_block,
        gpu_flag_line=gpu_flag_line,
        gpu_models_override=gpu_models_override,
    )

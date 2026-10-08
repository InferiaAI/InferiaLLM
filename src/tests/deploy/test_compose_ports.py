import os
from pathlib import Path
import yaml

REPO = os.environ.get("INFERIA_REPO") or str(Path(__file__).resolve().parents[3])
COMPOSE = os.path.join(REPO, "docker-compose.yml")

def _service(name):
    with open(COMPOSE) as f:
        data = yaml.safe_load(f)
    return data["services"][name]

def _app_service():
    return _service("app")

def test_app_publishes_app_port_and_no_legacy_ports():
    ports = [str(p) for p in _app_service().get("ports", [])]
    text = "\n".join(ports)
    assert "APP_PORT" in text, f"app must publish APP_PORT, got {ports}"
    for legacy in ("DASHBOARD_PORT", "FILTRATION_GATEWAY_PORT", "INFERENCE_GATEWAY_PORT"):
        assert legacy not in text, f"{legacy} replaced by APP_PORT"

def test_envoy_does_not_publish_its_admin_port():
    ports = [str(p) for p in _service("front-envoy").get("ports", [])]
    assert not any("9901" in p for p in ports), f"envoy admin published: {ports}"

def test_app_does_not_hardcode_mirror_base():
    env = _app_service().get("environment", [])
    # environment may be a list ("K=V") or a dict; normalize to text and assert
    # no hardcoded wlan0.in mirror base remains.
    text = "\n".join(env) if isinstance(env, list) else "\n".join(f"{k}={v}" for k, v in (env or {}).items())
    assert "inferiallm.wlan0.in" not in text, "remove the hardcoded INFERIA_MODEL_MIRROR_BASE override"
    # mirror base must pass through from .env (a ${INFERIA_MODEL_MIRROR_BASE...} ref) or be absent
    assert "INFERIA_MODEL_MIRROR_BASE=https://" not in text

def test_app_passes_app_port():
    env = _app_service().get("environment", [])
    text = "\n".join(env) if isinstance(env, list) else "\n".join(f"{k}={v}" for k, v in (env or {}).items())
    assert "APP_PORT" in text

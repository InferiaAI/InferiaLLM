import os, yaml

REPO = os.environ.get("INFERIA_REPO", "/host")
COMPOSE = os.path.join(REPO, "docker-compose.yml")

def _app_service():
    with open(COMPOSE) as f:
        data = yaml.safe_load(f)
    return data["services"]["app"]

def test_app_publishes_exactly_one_port():
    ports = _app_service().get("ports", [])
    assert len(ports) == 1, f"app should publish exactly one port, got {ports}"

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

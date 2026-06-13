import os, re, pytest

REPO = "/host"
ENV = os.path.join(REPO, ".env")
EXAMPLE = os.path.join(REPO, ".env.example")

SECRET_KEYS = {
    "SUPERADMIN_PASSWORD", "JWT_SECRET_KEY", "INTERNAL_API_KEY",
    "SECRET_ENCRYPTION_KEY", "LOG_ENCRYPTION_KEY", "POSTGRES_PASSWORD",
    "PG_ADMIN_PASSWORD", "REDIS_PASSWORD", "ELASTICSEARCH_PASSWORD",
}
DROPPED = {"INFERIA_HF_TOKEN", "NOSANA_PROD_KEY", "SOLANA_RPC_URL"}


def _parse(path):
    keys, kv = [], {}
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            keys.append(k)
            kv[k] = v.strip().strip('"').strip("'")
    return keys, kv


def test_example_invariants():
    # .env.example is committed and ALWAYS present
    keys, kv = _parse(EXAMPLE)
    for k in DROPPED:
        assert k not in keys, f"{k} must be dropped from .env.example"
    for k in SECRET_KEYS:
        assert kv.get(k, "") == "", f"{k} must be blank in committed .env.example (no real secret)"
    # DATABASE_URL must not embed a real-looking secret password
    db = kv.get("DATABASE_URL", "")
    assert "wlan0.in" not in "\n".join(f"{k}={v}" for k, v in kv.items())
    # single-port: APP_PORT present, the 3 old port vars gone
    assert "APP_PORT" in keys
    for old in ("DASHBOARD_PORT", "FILTRATION_GATEWAY_PORT", "INFERENCE_GATEWAY_PORT"):
        assert old not in keys, f"{old} replaced by APP_PORT"


@pytest.mark.skipif(not os.path.exists(ENV), reason="local .env absent (CI)")
def test_env_and_example_are_parallel():
    env_keys, _ = _parse(ENV)
    ex_keys, _ = _parse(EXAMPLE)
    assert env_keys == ex_keys, (
        f"key set+order must match exactly\n.env={env_keys}\n.env.example={ex_keys}"
    )
    for k in DROPPED:
        assert k not in env_keys


@pytest.mark.skipif(not os.path.exists(ENV), reason="local .env absent (CI)")
def test_env_keeps_real_secrets():
    _, kv = _parse(ENV)
    # local .env must STILL carry real secret values (not blanked)
    for k in ("JWT_SECRET_KEY", "SECRET_ENCRYPTION_KEY", "INTERNAL_API_KEY"):
        assert kv.get(k, ""), f"{k} must keep its real value in local .env"

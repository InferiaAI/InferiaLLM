"""The llm-d data-plane mode's resource builder (session-3 T2, plan WS2).

Pins the shape of the objects one InferiaLLM deployment becomes on Kubernetes
when its pool runs the llm-d data plane: a model-server Deployment, an
InferencePool, and an llm-d router (EPP + Envoy) with its config. These are pure
functions over dicts, so no cluster is needed.

Shapes follow the llm-d-router-standalone chart v1.0.0 and the llm-d v1.0.0
precise-prefix-cache-routing guide; deploy/kind/README.md lists the sources.
"""
import pytest
import yaml

from providers.k8s.llmd import resources as r


def _spec(**over):
    md = {"engine": "llm-d-inference-sim", "model_id": "sim-model", "replicas": 2}
    md.update(over.pop("metadata", {}))
    kw = dict(name="inferia-llmd-abc123", namespace="inferia-llmd",
              metadata=md, pool_id="pool-1")
    kw.update(over)
    return r.spec_from_metadata(**kw)


def _by_kind(objs):
    out = {}
    for o in objs:
        out.setdefault(o["kind"], []).append(o)
    return out


# --- ids and mode selection --------------------------------------------------
def test_instance_ids_carry_the_prefix():
    name = r.new_instance_name()
    assert name.startswith(r.LLMD_ID_PREFIX)
    assert r.is_llmd_instance(name)
    assert len(name) <= 63  # a DNS-1123 label; it names Services


def test_plain_k8s_ids_are_not_llmd():
    assert not r.is_llmd_instance("inferia-worker-abc123")
    assert not r.is_llmd_instance(None)
    assert not r.is_llmd_instance("")


@pytest.mark.parametrize("md,expected", [
    ({"dataplane": "llm-d"}, True),
    ({"dataplane": "LLM-D"}, True),
    ({"dataplane": "envoy-xds"}, False),
    ({}, False),
    (None, False),
])
def test_wants_llmd(md, expected):
    assert r.wants_llmd(md) is expected


# --- validation at the boundary ---------------------------------------------
def test_vllm_is_refused_until_render_endpoints_are_verified():
    # Precise prefix routing tokenizes through /v1/*/render on the model
    # servers; the pinned vLLM recipe (v0.22.1) is not verified to serve it.
    with pytest.raises(r.LlmdSpecError, match="not supported"):
        _spec(metadata={"engine": "vllm"})


def test_unknown_engine_is_refused_not_silently_defaulted():
    with pytest.raises(r.LlmdSpecError):
        _spec(metadata={"engine": "ollama"})


@pytest.mark.parametrize("model", ["", "a b", "x;rm -rf /", "m\nodel", "a" * 300, None])
def test_bad_model_names_are_refused(model):
    with pytest.raises(r.LlmdSpecError):
        _spec(metadata={"model_id": model})


@pytest.mark.parametrize("replicas", [0, -1, 33, "two"])
def test_bad_replica_counts_are_refused(replicas):
    with pytest.raises(r.LlmdSpecError):
        _spec(metadata={"replicas": replicas})


def test_replicas_default_to_one():
    md = {"engine": "llm-d-inference-sim", "model_id": "m"}
    s = r.spec_from_metadata(name="inferia-llmd-x", namespace="ns", metadata=md, pool_id="p")
    assert s.replicas == 1


def test_bad_service_type_is_refused():
    with pytest.raises(r.LlmdSpecError):
        _spec(metadata={"service_type": "ExternalName"})


def test_name_must_carry_the_prefix():
    with pytest.raises(r.LlmdSpecError):
        _spec(name="something-else")


# --- the object set ----------------------------------------------------------
def test_object_set():
    kinds = _by_kind(r.build_objects(_spec()))
    assert {k: len(v) for k, v in kinds.items()} == {
        "ConfigMap": 2, "Deployment": 2, "Service": 3, "InferencePool": 1,
    }


def test_every_object_is_labelled_for_cleanup():
    s = _spec()
    for o in r.build_objects(s):
        labels = o["metadata"]["labels"]
        assert labels[r.INSTANCE_LABEL] == s.name, o["metadata"]["name"]
        assert labels["app.kubernetes.io/managed-by"] == "inferiallm"
        assert o["metadata"]["namespace"] == s.namespace


def test_no_rbac_or_cluster_scoped_objects_per_deployment():
    # RBAC is a once-per-namespace bootstrap (deploy/kind/manifests/
    # llmd-namespace-bootstrap.yaml), so orchestration never needs RBAC verbs.
    kinds = {o["kind"] for o in r.build_objects(_spec())}
    assert not kinds & {"Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding",
                        "ServiceAccount", "Namespace"}


def test_names_are_unique():
    names = [(o["kind"], o["metadata"]["name"]) for o in r.build_objects(_spec())]
    assert len(names) == len(set(names))


def test_inference_pool_points_at_the_model_pods_and_the_epp():
    s = _spec()
    objs = r.build_objects(s)
    pool = _by_kind(objs)["InferencePool"][0]
    assert pool["apiVersion"] == "inference.networking.k8s.io/v1"
    assert pool["metadata"]["name"] == s.name
    sel = pool["spec"]["selector"]["matchLabels"]
    model = next(o for o in objs if o["kind"] == "Deployment"
                 and o["metadata"]["name"] == r.model_server_name(s.name))
    assert model["spec"]["template"]["metadata"]["labels"].items() >= sel.items()
    assert pool["spec"]["targetPorts"] == [{"number": 8000}]
    epp_ref = pool["spec"]["endpointPickerRef"]
    assert epp_ref["name"] == r.epp_service_name(s.name)
    assert epp_ref["port"] == {"number": 9002}
    assert epp_ref["failureMode"] == "FailOpen"


def test_epp_pods_are_not_selected_by_the_pool():
    s = _spec()
    objs = r.build_objects(s)
    pool = _by_kind(objs)["InferencePool"][0]
    sel = pool["spec"]["selector"]["matchLabels"]
    epp = next(o for o in objs if o["kind"] == "Deployment"
               and o["metadata"]["name"] == r.epp_name(s.name))
    labels = epp["spec"]["template"]["metadata"]["labels"]
    assert not labels.items() >= sel.items()


def test_only_model_pods_carry_the_instance_pod_label():
    # The adapter's log and pod helpers select on inferia-instance=<id>; they
    # must land on a model server, never on the router.
    s = _spec()
    objs = r.build_objects(s)
    deps = {o["metadata"]["name"]: o for o in objs if o["kind"] == "Deployment"}
    model_labels = deps[r.model_server_name(s.name)]["spec"]["template"]["metadata"]["labels"]
    epp_labels = deps[r.epp_name(s.name)]["spec"]["template"]["metadata"]["labels"]
    assert model_labels["inferia-instance"] == s.name
    assert "inferia-instance" not in epp_labels


def test_sim_model_server_args_publish_kv_events():
    s = _spec(metadata={"model_id": "org/model-1"})
    model = next(o for o in r.build_objects(s) if o["metadata"]["name"] == r.model_server_name(s.name))
    c = model["spec"]["template"]["spec"]["containers"][0]
    args = c["args"]
    assert "--model=org/model-1" in args
    assert "--enable-kvcache" in args  # bare form: v1.0.0 refuses "--enable-kvcache true"
    assert "--block-size=64" in args   # must equal the EPP tokenProcessorConfig.blockSizeTokens
    assert "--zmq-endpoint=tcp://*:5556" in args
    assert "--kv-events-replay-endpoint=tcp://*:5559" in args
    assert not any(a in ("true", "false") for a in args)
    env = {e["name"] for e in c["env"]}
    assert {"POD_IP", "POD_NAME", "POD_NAMESPACE"} <= env
    ports = {p["containerPort"] for p in c["ports"]}
    assert {8000, 5556, 5559} <= ports
    assert c["readinessProbe"]["httpGet"]["path"] == "/health"
    assert model["spec"]["replicas"] == 2


def test_model_server_runs_unprivileged():
    s = _spec()
    model = next(o for o in r.build_objects(s) if o["metadata"]["name"] == r.model_server_name(s.name))
    pod = model["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    sc = pod["containers"][0]["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}


def test_epp_config_names_the_model_and_matches_block_size():
    s = _spec(metadata={"model_id": "org/model-1"})
    cm = next(o for o in r.build_objects(s) if o["metadata"]["name"] == r.epp_config_name(s.name))
    cfg = yaml.safe_load(cm["data"][r.EPP_CONFIG_FILE])
    assert cfg["kind"] == "EndpointPickerConfig"
    plugins = {p["type"]: p.get("parameters", {}) for p in cfg["plugins"]}
    assert plugins["token-producer"]["modelName"] == "org/model-1"
    assert plugins["precise-prefix-cache-producer"]["tokenProcessorConfig"]["blockSizeTokens"] == 64
    kv = plugins["precise-prefix-cache-producer"]["kvEventsConfig"]["podDiscoveryConfig"]
    assert kv == {"socketPort": 5556, "replaySocketPort": 5559}
    profile = cfg["schedulingProfiles"][0]
    assert [p["pluginRef"] for p in profile["plugins"]] == [
        "prefix-cache-affinity-filter", "token-load-scorer"]


def test_model_name_cannot_inject_yaml_into_the_epp_config():
    # Validation refuses newlines, but the config is also built as a dict and
    # dumped, never string-formatted.
    s = _spec(metadata={"model_id": "org/m:v1"})
    cm = next(o for o in r.build_objects(s) if o["metadata"]["name"] == r.epp_config_name(s.name))
    cfg = yaml.safe_load(cm["data"][r.EPP_CONFIG_FILE])
    assert cfg["plugins"][0]["parameters"]["modelName"] == "org/m:v1"


def test_epp_deployment():
    s = _spec()
    epp = next(o for o in r.build_objects(s) if o["metadata"]["name"] == r.epp_name(s.name))
    spec = epp["spec"]
    assert spec["replicas"] == 1  # the KV index and load accounting are per process
    assert spec["strategy"] == {"type": "Recreate"}
    pod = spec["template"]["spec"]
    assert pod["serviceAccountName"] == r.EPP_SERVICE_ACCOUNT
    containers = {c["name"]: c for c in pod["containers"]}
    assert set(containers) == {"envoy-proxy", "epp"}
    args = containers["epp"]["args"]
    assert args[args.index("--pool-name") + 1] == s.name
    assert args[args.index("--pool-namespace") + 1] == s.namespace
    assert args[args.index("--pool-group") + 1] == "inference.networking.k8s.io"
    assert args[args.index("--config-file") + 1] == f"/config/{r.EPP_CONFIG_FILE}"
    assert "--ha-enable-leader-election=false" in args
    assert "@sha256:" in containers["epp"]["image"]
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["config"]["configMap"]["name"] == r.envoy_config_name(s.name)
    assert vols["plugins-config-volume"]["configMap"]["name"] == r.epp_config_name(s.name)


def test_envoy_fallback_points_at_this_deployments_model_servers():
    s = _spec()
    cm = next(o for o in r.build_objects(s) if o["metadata"]["name"] == r.envoy_config_name(s.name))
    cfg = yaml.safe_load(cm["data"]["envoy.yaml"])
    fallback = next(c for c in cfg["static_resources"]["clusters"]
                    if c["name"] == "inference_pool_fallback")
    sock = fallback["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"]
    assert sock == {
        "address": f"{r.fallback_service_name(s.name)}.{s.namespace}.svc.cluster.local",
        "port_value": 8000,
    }
    # Nothing from the release the vendored config was rendered with survives
    # (_regenerate.py renders release "render" in namespace "render").
    assert "render-epp-model-servers" not in cm["data"]["envoy.yaml"]
    assert ".render.svc" not in cm["data"]["envoy.yaml"]


def test_envoy_config_is_per_deployment_not_the_charts_fixed_name():
    # The chart names this ConfigMap "envoy", which collides between two
    # deployments in one namespace.
    a = _spec(name="inferia-llmd-aaaaaa")
    b = _spec(name="inferia-llmd-bbbbbb")
    assert r.envoy_config_name(a.name) != r.envoy_config_name(b.name)


def test_services():
    s = _spec(metadata={"service_type": "NodePort"})
    svcs = {o["metadata"]["name"]: o["spec"] for o in r.build_objects(s) if o["kind"] == "Service"}
    gw = svcs[r.gateway_service_name(s.name)]
    # The only Service that may leave the cluster carries only the proxy port.
    assert gw["type"] == "NodePort"
    assert [p["port"] for p in gw["ports"]] == [8081]
    epp = svcs[r.epp_service_name(s.name)]
    assert epp["type"] == "ClusterIP"
    assert {p["port"] for p in epp["ports"]} == {9002, 9090}
    fb = svcs[r.fallback_service_name(s.name)]
    assert fb["clusterIP"] == "None"


def test_gateway_service_is_named_after_the_instance():
    # KubernetesAdapter._resolve_url(name) reads back the Service named after
    # the instance, so the gateway must own that name.
    s = _spec()
    assert r.gateway_service_name(s.name) == s.name


def test_service_type_defaults_to_cluster_ip(monkeypatch):
    monkeypatch.delenv("K8S_SERVICE_TYPE", raising=False)
    s = _spec()
    gw = next(o for o in r.build_objects(s)
              if o["kind"] == "Service" and o["metadata"]["name"] == r.gateway_service_name(s.name))
    assert gw["spec"]["type"] == "ClusterIP"


def test_service_type_follows_the_adapters_env_setting(monkeypatch):
    monkeypatch.setenv("K8S_SERVICE_TYPE", "NodePort")
    assert _spec().service_type == "NodePort"


def test_namespace_comes_from_env_with_a_default(monkeypatch):
    monkeypatch.delenv("K8S_LLMD_NAMESPACE", raising=False)
    assert r.llmd_namespace() == "inferia-llmd"
    monkeypatch.setenv("K8S_LLMD_NAMESPACE", "team-a")
    assert r.llmd_namespace() == "team-a"


@pytest.mark.parametrize("bad", ["Team_A", "-x", "a" * 64, "a/b"])
def test_bad_namespace_from_env_is_refused(monkeypatch, bad):
    monkeypatch.setenv("K8S_LLMD_NAMESPACE", bad)
    with pytest.raises(r.LlmdSpecError):
        r.llmd_namespace()


def test_deployment_id_is_labelled_when_known():
    s = _spec(metadata={"deployment_id": "0b5c-11"})
    for o in r.build_objects(s):
        assert o["metadata"]["labels"]["inferia-deployment"] == "0b5c-11"

"""Tests for engine recipes.

The first of these is the one that matters: ports, model directories and
health paths used to be three dicts in the Kubernetes adapter, and the
migration is only safe if every engine resolves to exactly what it had. The
old tables are inlined here as literals so they are a fixed reference rather
than something that moves with the code under test.
"""

import pytest

from orchestration import recipes


# The three dicts this replaced, verbatim from k8s_adapter.py before the move.
_OLD_PORTS = {
    "ollama": 11434,
    "vllm": 8000,
    "vllm-omni": 8000,
}
_OLD_MODEL_DIRS = {
    "ollama": "/root/.ollama",
    "vllm": "/root/.cache/huggingface",
    "vllm-omni": "/root/.cache/huggingface",
}
_OLD_HEALTH_PATHS = {
    "ollama": "/api/version",
}
_OLD_DEFAULT_PORT = 8000


@pytest.fixture(autouse=True)
def _fresh():
    """Recipes are cached, and these tests change what would be loaded."""
    recipes.reload()
    yield
    recipes.reload()


# ---------------------------------------------------------------------------
# Equivalence with what was replaced
# ---------------------------------------------------------------------------
# Every engine and profile that is deployable. vLLM is GPU-only: its CPU
# backend is a different image, so there is no cpu profile to resolve.
_PAIRS = [
    ("ollama", "cpu"),
    ("ollama", "gpu"),
    ("vllm", "gpu"),
    ("vllm-omni", "gpu"),
]


@pytest.mark.parametrize("engine,profile", _PAIRS)
def test_every_engine_resolves_to_what_it_had(engine, profile):
    r = recipes.resolve(engine, profile)

    assert r.port == _OLD_PORTS[engine], "port changed"
    assert r.model_dir == _OLD_MODEL_DIRS.get(engine), "model dir changed"
    assert r.health_path == _OLD_HEALTH_PATHS.get(engine), "health path changed"


@pytest.mark.parametrize("engine", ["vllm", "vllm-omni"])
def test_vllm_has_no_cpu_profile(engine):
    """Deliberate. vLLM's CPU backend is a separate image, so a zero-GPU vLLM
    deployment is rejected rather than scheduled and left unable to start."""
    with pytest.raises(recipes.UnknownProfile) as exc:
        recipes.resolve(engine, "cpu")

    assert "gpu" in str(exc.value), "the error should say what is available"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
def test_a_cpu_engine_reserves_little_and_is_not_capped():
    """The ceiling is what made this slow - 31.3s capped at one core
    against 9.2s at three - not the reservation. A ceiling would be
    justified by pinned cores, but those need a kubelet setting the
    adapter cannot turn on, so the reservation stays small and the
    ceiling goes."""
    r = recipes.resolve("ollama", "cpu")

    assert r.cpu_burstable is True
    assert r.cpu_request == "1"


def test_a_gpu_engine_is_burstable():
    """There the CPU does tokenisation and scheduling, and a ceiling produces
    the latency spikes an SLO exists to prevent."""
    for engine in ("ollama", "vllm"):
        assert recipes.resolve(engine, "gpu").cpu_burstable is True


def test_the_pull_policy_is_declared_not_inferred():
    """Kubernetes infers Always from a latest tag, and Always fails a
    container when the registry is unreachable even if the image is local."""
    for engine, profile in _PAIRS:
        assert recipes.resolve(engine, profile).image_pull_policy == "IfNotPresent"


def test_the_shipped_engines_are_the_ones_that_were_there():
    """A recipe added by mistake is as much a change as one removed."""
    assert recipes.known_engines() == sorted(_OLD_PORTS)


def test_an_unknown_engine_still_gets_defaults():
    """Unchanged from the old behaviour: a warning and a usable pod, not a
    failure. Whether that is right is a separate question."""
    r = recipes.resolve("something-new", "cpu")

    assert r.port == _OLD_DEFAULT_PORT
    assert r.model_dir is None, "an unknown engine gets no model volume"
    assert r.health_path is None, "and no HTTP health check"


def test_engine_names_are_matched_case_insensitively():
    assert recipes.resolve("Ollama", "cpu").port == _OLD_PORTS["ollama"]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
def test_profile_follows_the_gpu_allocation():
    assert recipes.profile_for(0) == "cpu"
    assert recipes.profile_for(1) == "gpu"
    assert recipes.profile_for(None) == "cpu"


def test_a_known_engine_missing_a_profile_raises(tmp_path, monkeypatch):
    """Distinct from an unknown engine. Asking for a profile an engine does
    not ship is a request for something real, and answering it with defaults
    produces a pod that schedules and cannot run."""
    (tmp_path / "gpu-only.yaml").write_text(
        "engines:\n"
        "  gpu-only-engine:\n"
        "    port: 9000\n"
        "    profiles:\n"
        "      gpu: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFERIA_RECIPES_DIR", str(tmp_path))
    recipes.reload()

    assert recipes.resolve("gpu-only-engine", "gpu").port == 9000

    with pytest.raises(recipes.UnknownProfile):
        recipes.resolve("gpu-only-engine", "cpu")


# ---------------------------------------------------------------------------
# The override directory
# ---------------------------------------------------------------------------
def test_a_dropped_in_file_adds_an_engine(tmp_path, monkeypatch):
    """The point of the directory: a customer engine is a file, not a code
    change, and it has to work from an installed wheel."""
    (tmp_path / "customer.yaml").write_text(
        "engines:\n"
        "  customer-engine:\n"
        "    port: 7000\n"
        "    model_dir: /models\n"
        "    health_path: /healthz\n"
        "    profiles:\n"
        "      cpu: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFERIA_RECIPES_DIR", str(tmp_path))
    recipes.reload()

    r = recipes.resolve("customer-engine", "cpu")

    assert (r.port, r.model_dir, r.health_path) == (7000, "/models", "/healthz")
    assert recipes.resolve("ollama", "cpu").port == _OLD_PORTS["ollama"], \
        "adding one engine must not disturb the others"


def test_a_dropped_in_file_can_replace_a_shipped_engine(tmp_path, monkeypatch):
    (tmp_path / "ollama.yaml").write_text(
        "engines:\n"
        "  ollama:\n"
        "    port: 1234\n"
        "    profiles:\n"
        "      cpu: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFERIA_RECIPES_DIR", str(tmp_path))
    recipes.reload()

    assert recipes.resolve("ollama", "cpu").port == 1234


def test_no_override_directory_changes_nothing(monkeypatch):
    monkeypatch.delenv("INFERIA_RECIPES_DIR", raising=False)
    recipes.reload()

    assert recipes.resolve("ollama", "cpu").port == _OLD_PORTS["ollama"]


def test_an_unreadable_override_directory_is_ignored(monkeypatch):
    """A misconfigured path must not take the control plane down."""
    monkeypatch.setenv("INFERIA_RECIPES_DIR", "/no/such/directory")
    recipes.reload()

    assert recipes.resolve("ollama", "cpu").port == _OLD_PORTS["ollama"]


# ---------------------------------------------------------------------------
# Profile-level overrides
# ---------------------------------------------------------------------------
def test_a_profile_overrides_the_engine(tmp_path, monkeypatch):
    """What the profile dimension exists for: the same engine wanting
    different settings on different hardware."""
    (tmp_path / "split.yaml").write_text(
        "engines:\n"
        "  split-engine:\n"
        "    port: 1000\n"
        "    model_dir: /shared\n"
        "    profiles:\n"
        "      cpu: {}\n"
        "      gpu:\n"
        "        port: 2000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFERIA_RECIPES_DIR", str(tmp_path))
    recipes.reload()

    assert recipes.resolve("split-engine", "cpu").port == 1000
    assert recipes.resolve("split-engine", "gpu").port == 2000
    assert recipes.resolve("split-engine", "gpu").model_dir == "/shared", \
        "a profile inherits what it does not override"

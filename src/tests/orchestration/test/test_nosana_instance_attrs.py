"""Verify NosanaAdapter cache attributes are per-instance, not shared."""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock


import providers

# ---------------------------------------------------------------------------
# Lightweight stubs for heavy deps that nosana_adapter imports at module level
# ---------------------------------------------------------------------------

_ADAPTER_FILE = os.path.normpath(
    os.path.join(
        os.path.dirname(providers.__file__),
        "nosana",
        "nosana_adapter.py",
    )
)


def _load_adapter_class():
    """Load NosanaAdapter directly from the source file in this worktree."""

    # Stub config
    fake_settings = MagicMock()
    fake_settings.internal_api_key = "test-key"
    fake_settings.nosana_sidecar_url = "http://localhost:9999"
    config_mod = types.ModuleType("orchestration.config")
    config_mod.settings = fake_settings
    sys.modules["orchestration.config"] = config_mod

    # Stub job_builder
    jb_path = (
        "orchestration.provisioning.engine"
        ".adapters.nosana.job_builder"
    )
    jb_mod = types.ModuleType(jb_path)
    jb_mod.build_job_definition = MagicMock()
    jb_mod.create_training_job = MagicMock()
    jb_mod.INTERNAL_API_KEY = "stub-key"
    sys.modules[jb_path] = jb_mod

    # Load the module from the exact file path
    mod_name = "nosana_adapter_under_test"
    spec = importlib.util.spec_from_file_location(mod_name, _ADAPTER_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.NosanaAdapter


NosanaAdapter = _load_adapter_class()


class TestNosanaInstanceIsolation:
    """_resources_cache and _last_discovery_time must be per-instance."""

    def test_resources_cache_not_shared(self):
        a = NosanaAdapter()
        b = NosanaAdapter()

        a._resources_cache.append({"gpu_type": "A100"})

        assert b._resources_cache == [], (
            "_resources_cache leaked across instances"
        )

    def test_last_discovery_time_not_shared(self):
        a = NosanaAdapter()
        b = NosanaAdapter()

        a._last_discovery_time = 999.0

        assert b._last_discovery_time == 0.0, (
            "_last_discovery_time leaked across instances"
        )

    def test_cache_duration_is_class_constant(self):
        """CACHE_DURATION is an immutable int -- safe to keep class-level."""
        assert NosanaAdapter.CACHE_DURATION == 300
        a = NosanaAdapter()
        assert a.CACHE_DURATION == 300

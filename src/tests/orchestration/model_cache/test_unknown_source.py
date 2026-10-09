"""A cache row whose source has no layout must not take the cache with it.

`_dir_for` used to fall back to `ollama_root()`, and both callers `rmtree` what
it returns, so deleting one such row erased every cached Ollama model.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestration.models.model_cache.api import AddModelBody
from orchestration.models.model_cache.eviction import EvictionManager
from orchestration.models.model_cache.paths import CachePaths


def _row(source: str, model_id: str = "qwen2", revision: str = "0.5b") -> dict:
    return {"id": 1, "source": source, "model_id": model_id, "revision": revision}


@pytest.fixture
def manager(tmp_path):
    return EvictionManager(
        repo=None, paths=CachePaths(str(tmp_path)), max_bytes=1, in_use=lambda: set()
    )


class TestTheDirectoryResolver:

    def test_a_known_source_still_resolves(self, manager, tmp_path):
        assert manager._dir_for(_row("hf")) == CachePaths(str(tmp_path)).hf_dir(
            "qwen2", "0.5b"
        )
        assert manager._dir_for(_row("ollama")) == CachePaths(
            str(tmp_path)
        ).ollama_dir("qwen2", "0.5b")

    @pytest.mark.parametrize("source", ["local", "oci", "", "HF", "unknown"])
    def test_an_unknown_source_resolves_to_nothing(self, manager, source):
        assert manager._dir_for(_row(source)) is None

    def test_it_never_returns_a_cache_root(self, manager, tmp_path):
        """The specific regression: rmtree on a root wipes every model."""
        paths = CachePaths(str(tmp_path))
        roots = {paths.ollama_root(), paths.root}
        for source in ("local", "oci", "unknown"):
            assert manager._dir_for(_row(source)) not in roots


class TestTheRequestSchema:

    @pytest.mark.parametrize("source", ["hf", "ollama"])
    def test_a_supported_source_is_accepted(self, source):
        assert AddModelBody(source=source, model_id="m").source == source

    @pytest.mark.parametrize("source", ["local", "oci", "HF", ""])
    def test_an_unsupported_source_is_refused(self, source):
        """Rejecting at the door means no such row is ever created."""
        with pytest.raises(ValidationError):
            AddModelBody(source=source, model_id="m")

    def test_the_default_is_unchanged(self):
        assert AddModelBody(model_id="m").source == "hf"

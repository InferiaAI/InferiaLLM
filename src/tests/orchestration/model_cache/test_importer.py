"""Importing a staged model is the only way to fill the cache with no egress.

Covers the two staging formats an operator already has — a `hf download
--local-dir` directory and a copy of ~/.ollama/models — and the containment
that stops an import reading outside the staging directory.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from orchestration.models.model_cache import importer
from orchestration.models.model_cache.paths import CachePaths

COMMIT = "a" * 40


@pytest.fixture
def cache(tmp_path):
    cp = CachePaths(str(tmp_path))
    cp.import_root().mkdir(parents=True)
    return cp


def _hf_staging(cache, name="staged", *, with_metadata=True):
    stage = cache.import_root() / name
    stage.mkdir()
    (stage / "config.json").write_text('{"model_type":"llama"}')
    (stage / "model.safetensors").write_bytes(b"W" * 4096)
    if with_metadata:
        meta = stage / ".cache" / "huggingface" / "download"
        meta.mkdir(parents=True)
        for f, etag in (("config.json", "etag-cfg"), ("model.safetensors", "etag-w")):
            (meta / f"{f}.metadata").write_text(f"{COMMIT}\n{etag}\n1700000000.0\n")
    return stage


def _ollama_staging(cache, name="ollama-staged", tag="0.5b"):
    stage = cache.import_root() / name
    blobs = stage / "blobs"
    blobs.mkdir(parents=True)
    layers = []
    for body in (b"GGUF-WEIGHTS", b"TEMPLATE"):
        digest = hashlib.sha256(body).hexdigest()
        (blobs / f"sha256-{digest}").write_bytes(body)
        layers.append({"digest": f"sha256:{digest}", "size": len(body)})
    man = stage / "manifests" / "registry.ollama.ai" / "library" / "qwen2"
    man.mkdir(parents=True)
    (man / tag).write_text(json.dumps({"layers": layers}))
    return stage, layers


class TestStagingContainment:
    """/hf/ skips user auth, so an unconstrained path would let anyone able to
    create a model publish any file the control plane can read."""

    def test_a_name_inside_staging_is_allowed(self, cache):
        (cache.import_root() / "good").mkdir()
        assert cache.resolve_import("good").name == "good"

    @pytest.mark.parametrize(
        "name", ["..", "../secret.env", "../../etc/passwd", "/etc/passwd", ""]
    )
    def test_anything_escaping_is_refused(self, cache, name):
        with pytest.raises(ValueError):
            cache.resolve_import(name)

    def test_staging_does_not_count_towards_the_eviction_cap(self, cache):
        """Imported files are hard-linked, so counting both charges twice."""
        _hf_staging(cache)
        assert cache.total_bytes() < cache.dir_size_bytes(cache.root)


@pytest.mark.skipif(
    not hasattr(__import__("os"), "symlink"), reason="no symlink support"
)
class TestSymlinksInsideStaging:
    """resolve_import only guards the entry itself. Inside it, is_file() and
    os.link both follow links, so a link to the control plane's .env would be
    copied into a cache that /hf/ serves with no auth."""

    @staticmethod
    def _link(target, link):
        import os
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("creating a symlink needs privilege on this platform")

    def test_a_linked_file_in_an_hf_tree_is_refused(self, cache, tmp_path):
        stage = _hf_staging(cache)
        secret = tmp_path / "secret.env"
        secret.write_text("INTERNAL_API_KEY=xyz")
        self._link(secret, stage / "notes.txt")

        with pytest.raises(ValueError, match="symlink"):
            importer.hf_files(stage)

    def test_a_linked_ollama_blob_is_refused(self, cache, tmp_path):
        stage, layers = _ollama_staging(cache)
        secret = tmp_path / "secret.env"
        secret.write_text("INTERNAL_API_KEY=xyz")
        victim = stage / "blobs" / layers[0]["digest"].replace(":", "-")
        victim.unlink()
        self._link(secret, victim)

        manifest = importer.ollama_manifest(stage, "qwen2", "0.5b")
        with pytest.raises(ValueError, match="symlink"):
            importer.ollama_import(
                stage, cache.ollama_dir("qwen2", "0.5b"), manifest,
                importer.ollama_layers(manifest),
            )


class TestTheHuggingFaceFormat:

    def test_bookkeeping_is_not_imported(self, cache):
        stage = _hf_staging(cache)
        listed = {f["path"] for f in importer.hf_files(stage)}
        assert listed == {"config.json", "model.safetensors"}

    def test_the_recorded_commit_and_etags_are_used(self, cache):
        stage = _hf_staging(cache)
        meta = importer.hf_import(
            stage, cache.hf_dir("meta/llama", "main"), importer.hf_files(stage)
        )
        assert meta["commit_sha"] == COMMIT
        assert meta["file_meta"]["config.json"]["etag"] == "etag-cfg"
        assert meta["file_meta"]["model.safetensors"]["size"] == 4096

    def test_the_files_land_in_the_cache(self, cache):
        stage = _hf_staging(cache)
        dest = cache.hf_dir("meta/llama", "main")
        importer.hf_import(stage, dest, importer.hf_files(stage))
        assert (dest / "config.json").is_file()
        assert (dest / "model.safetensors").read_bytes() == b"W" * 4096

    def test_it_links_rather_than_copies(self, cache):
        stage = _hf_staging(cache)
        dest = cache.hf_dir("meta/llama", "main")
        importer.hf_import(stage, dest, importer.hf_files(stage))
        assert (dest / "config.json").stat().st_ino == (
            stage / "config.json"
        ).stat().st_ino


class TestWithoutRecordedMetadata:
    """A directory copied by hand has no .cache, and must still import."""

    def test_the_commit_is_synthesised_as_40_hex(self, cache):
        stage = _hf_staging(cache, with_metadata=False)
        meta = importer.hf_import(
            stage, cache.hf_dir("x/a", "main"), importer.hf_files(stage)
        )
        sha = meta["commit_sha"]
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)

    def test_identical_content_gives_the_same_commit(self, cache):
        a = _hf_staging(cache, "a", with_metadata=False)
        b = _hf_staging(cache, "b", with_metadata=False)
        first = importer.hf_import(a, cache.hf_dir("x/a", "main"), importer.hf_files(a))
        second = importer.hf_import(b, cache.hf_dir("x/b", "main"), importer.hf_files(b))
        assert first["commit_sha"] == second["commit_sha"]

    def test_different_content_gives_a_different_commit(self, cache):
        a = _hf_staging(cache, "a", with_metadata=False)
        b = _hf_staging(cache, "b", with_metadata=False)
        (b / "config.json").write_text('{"model_type":"mistral"}')
        first = importer.hf_import(a, cache.hf_dir("x/a", "main"), importer.hf_files(a))
        second = importer.hf_import(b, cache.hf_dir("x/b", "main"), importer.hf_files(b))
        assert first["commit_sha"] != second["commit_sha"]


class TestTheOllamaFormat:

    def test_a_bare_name_resolves_under_library(self, cache):
        """mirror_ollama._model_id strips library/ again when serving."""
        stage, _ = _ollama_staging(cache)
        assert importer.ollama_manifest(stage, "qwen2", "0.5b").is_file()

    def test_an_unknown_model_raises(self, cache):
        stage, _ = _ollama_staging(cache)
        with pytest.raises(FileNotFoundError):
            importer.ollama_manifest(stage, "qwen2", "7b")

    def test_the_manifest_and_blobs_land_in_the_cache(self, cache):
        stage, layers = _ollama_staging(cache)
        dest = cache.ollama_dir("qwen2", "0.5b")
        manifest = importer.ollama_manifest(stage, "qwen2", "0.5b")
        importer.ollama_import(
            stage, dest, manifest, importer.ollama_layers(manifest)
        )
        assert (dest / "manifest.json").is_file()
        for layer in layers:
            assert (dest / layer["digest"].replace(":", "_")).is_file()

    def test_a_tampered_blob_is_refused(self, cache):
        stage, layers = _ollama_staging(cache)
        victim = stage / "blobs" / layers[0]["digest"].replace(":", "-")
        victim.write_bytes(b"TAMPERED")
        manifest = importer.ollama_manifest(stage, "qwen2", "0.5b")
        with pytest.raises(ValueError, match="digest"):
            importer.ollama_import(
                stage, cache.ollama_dir("qwen2", "0.5b"), manifest,
                importer.ollama_layers(manifest),
            )

    def test_a_missing_blob_is_refused(self, cache):
        stage, layers = _ollama_staging(cache)
        (stage / "blobs" / layers[0]["digest"].replace(":", "-")).unlink()
        manifest = importer.ollama_manifest(stage, "qwen2", "0.5b")
        with pytest.raises(FileNotFoundError):
            importer.ollama_import(
                stage, cache.ollama_dir("qwen2", "0.5b"), manifest,
                importer.ollama_layers(manifest),
            )

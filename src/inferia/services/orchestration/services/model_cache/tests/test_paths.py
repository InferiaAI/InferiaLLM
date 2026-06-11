"""Tests for CachePaths — cache path layout and traversal safety."""
from __future__ import annotations

from inferia.services.orchestration.services.model_cache.paths import CachePaths


def test_hf_dir_is_sanitized_and_scoped(tmp_path):
    cp = CachePaths(str(tmp_path))
    d = cp.hf_dir("meta-llama/Llama-3", "main")
    assert str(d).startswith(str(tmp_path))
    assert ".." not in str(d)  # no path traversal from model id


def test_dir_size_sums_files(tmp_path):
    cp = CachePaths(str(tmp_path))
    d = cp.hf_dir("a/b", "main")
    d.mkdir(parents=True)
    (d / "f").write_bytes(b"x" * 10)
    assert cp.dir_size_bytes(d) == 10
    assert cp.total_bytes() == 10


def test_traversal_attempt_stays_under_root(tmp_path):
    """A model_id containing '..' or nested traversal cannot escape the cache root."""
    cp = CachePaths(str(tmp_path))

    # Direct parent-dir traversal: "../etc"
    d1 = cp.hf_dir("../etc", "main")
    assert str(d1).startswith(str(tmp_path)), (
        f"Path {d1!r} escaped cache root {tmp_path!r}"
    )

    # Multi-level traversal: "a/../../x"
    d2 = cp.hf_dir("a/../../x", "main")
    assert str(d2).startswith(str(tmp_path)), (
        f"Path {d2!r} escaped cache root {tmp_path!r}"
    )

    # Traversal disguised in revision: e.g. "../../etc/passwd"
    d3 = cp.hf_dir("legit-model", "../../etc/passwd")
    assert str(d3).startswith(str(tmp_path)), (
        f"Path {d3!r} escaped cache root {tmp_path!r}"
    )


def test_ollama_model_dir_matches_ollama_dir_parent(tmp_path):
    from inferia.services.orchestration.services.model_cache.paths import CachePaths
    p = CachePaths(str(tmp_path))
    # The blob mirror's model root must equal the parent of any revision dir.
    assert p.ollama_dir("ns/gemma3", "4b").parent == p.ollama_model_dir("ns/gemma3")
    # And it stays under the ollama root (sanitised).
    assert p.ollama_model_dir("../../etc").resolve().is_relative_to(p.ollama_root().resolve())

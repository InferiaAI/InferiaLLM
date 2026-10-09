"""Cache path layout helpers.

All returned paths are guaranteed to be rooted under ``CachePaths.root``.
The sanitiser prevents path-traversal via model_id or revision strings.
"""
from __future__ import annotations

import re
from pathlib import Path

# Allow alphanumerics, dots, underscores, and hyphens.
# Everything else (including '/' and ':') becomes '_'.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(part: str) -> str:
    """Return a filesystem-safe segment that cannot escape the cache root.

    Steps:
    1. Collapse any unsafe character (incl. ``/``) to ``_``.
    2. If the result is composed only of dots (i.e. ``.`` or ``..`` or
       ``...``), replace it with ``_``.  This prevents OS-level traversal
       even if the caller splits on ``/`` first and hands us a bare ``..``
       segment.
    3. Strip leading/trailing ``_`` and fall back to ``_`` if empty.
    """
    result = _SAFE.sub("_", part).strip("_") or "_"
    # A segment like ".." survives step 1 because '.' is in the safe set.
    # Guard: any segment that is *only* dots is a traversal attempt.
    if re.fullmatch(r"\.+", result):
        result = "_"
    return result or "_"


class CachePaths:
    """Filesystem path calculator for the model cache."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def hf_dir(self, model_id: str, revision: str) -> Path:
        """Return the directory for a HuggingFace model snapshot.

        ``model_id`` may contain ``/`` (e.g. ``meta-llama/Llama-3``); each
        segment is sanitised independently so ``../etc`` cannot escape.
        ``revision`` is also sanitised.
        """
        segs = [_sanitize(s) for s in model_id.split("/")]
        return self.root / "hf" / Path(*segs) / _sanitize(revision)

    def ollama_root(self) -> Path:
        """Return the root directory for Ollama blobs/manifests."""
        return self.root / "ollama"

    def ollama_model_dir(self, model_id: str) -> Path:
        """Return the per-model root (all revisions) for an Ollama model,
        sanitised identically to ``ollama_dir``. The /v2 blob mirror uses this
        so its blob lookups land on the SAME path the downloader wrote to
        (a raw ``ollama_root()/model_id`` join misses sanitised/namespaced ids)."""
        return self.ollama_root() / _sanitize(model_id)

    def ollama_dir(self, model_id: str, revision: str) -> Path:
        """Return the per-model directory for Ollama blobs.

        Using a per-model dir means eviction/delete removes only this model's
        blobs rather than wiping the entire ollama cache.
        """
        return self.ollama_model_dir(model_id) / _sanitize(revision)

    def dir_size_bytes(self, d: Path) -> int:
        """Return the total size in bytes of all files under *d*."""
        if not d.exists():
            return 0
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

    def import_root(self) -> Path:
        """Staging directory an operator places model files in.

        Inside the cache volume so an import hard-links rather than copies,
        and so a request naming a path outside it can be refused outright.
        """
        return self.root / "import"

    def resolve_import(self, name: str) -> Path:
        """The staging entry *name* refers to, or raise if it escapes.

        Imports reach a route that does not re-check the caller, so an
        unconstrained path would let anyone able to create a model publish
        any file the control plane can read.
        """
        root = self.import_root().resolve()
        raw = root / name
        # Before resolve(), which would follow it and hide the hop.
        if raw.is_symlink():
            raise ValueError(f"import path is a symlink: {name!r}")
        candidate = raw.resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError(f"import path escapes the staging directory: {name!r}")
        return candidate

    def total_bytes(self) -> int:
        """Return the total size in bytes of the cache, excluding staging.

        Imported files are hard-linked out of staging, so counting both would
        charge every imported model to the eviction cap twice.
        """
        return self.dir_size_bytes(self.root) - self.dir_size_bytes(self.import_root())

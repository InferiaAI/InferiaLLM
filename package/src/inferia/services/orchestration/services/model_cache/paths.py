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

    def dir_size_bytes(self, d: Path) -> int:
        """Return the total size in bytes of all files under *d*."""
        if not d.exists():
            return 0
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

    def total_bytes(self) -> int:
        """Return the total size in bytes of the entire cache root."""
        return self.dir_size_bytes(self.root)

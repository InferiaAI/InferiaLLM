"""Read a model out of the staging directory into the cache.

Two formats, each the output of the tool an operator already has:

* ``hf`` — a directory from ``hf download <repo> --local-dir <dir>``. Its
  ``.cache/huggingface/download/*.metadata`` files hold the commit and each
  file's etag, which is exactly what the mirror needs to answer offline.
* ``ollama`` — a copy of ``~/.ollama/models``, with ``manifests/`` and
  ``blobs/``.

Imports are recorded under the format's real cache source. There is no
``local`` source: lookups, eviction and both mirrors key on ``hf`` or
``ollama``, so a row under any other value is never found.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

_HF_METADATA_DIR = Path(".cache") / "huggingface" / "download"
_READ_CHUNK = 8 * 1024 * 1024


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_READ_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link *src* to *dst*, copying if the filesystem refuses.

    Staging lives inside the cache volume so the link normally succeeds and
    the import moves no bytes.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# HuggingFace
# ---------------------------------------------------------------------------

def _hf_recorded_metadata(root: Path, rel: str) -> tuple[str | None, str | None]:
    """The commit and etag huggingface_hub recorded for *rel*, if present.

    The metadata file is three lines: commit, etag, timestamp.
    """
    meta = root / _HF_METADATA_DIR / (rel + ".metadata")
    if not meta.is_file():
        return None, None
    try:
        lines = meta.read_text().splitlines()
    except OSError:
        return None, None
    commit = lines[0].strip() if len(lines) > 0 else None
    etag = lines[1].strip() if len(lines) > 1 else None
    return (commit or None), (etag or None)


def _reject_symlink(p: Path, root: Path) -> None:
    """Refuse a symlink anywhere in a staged tree.

    ``resolve_import`` only guards the entry itself. Inside it, ``is_file()``
    and ``os.link`` both follow links, so a link to the control plane's .env
    would be copied into the cache — which ``/hf/`` then serves with no auth.
    """
    if p.is_symlink():
        raise ValueError(f"staged tree contains a symlink: {p.relative_to(root)}")


def hf_files(root: Path) -> list[dict]:
    """Every importable file under *root*, with its size.

    ``.cache/`` is huggingface_hub's bookkeeping and is not part of the model.
    """
    out = []
    for p in sorted(root.rglob("*")):
        _reject_symlink(p, root)
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".cache/"):
            continue
        out.append({"path": rel, "size": p.stat().st_size})
    return out


def hf_import(root: Path, dest: Path, files: list[dict]) -> dict:
    """Link *files* into *dest* and return ``{commit_sha, file_meta}``.

    A directory with no recorded metadata still imports: each file's sha256
    becomes its etag, and the commit is a sha1 over the sorted (path, sha256)
    pairs so identical content always yields the same commit.
    """
    file_meta: dict[str, dict] = {}
    recorded_commit: str | None = None
    digest_pairs: list[tuple[str, str]] = []

    for f in files:
        rel = f["path"]
        src = root / rel
        commit, etag = _hf_recorded_metadata(root, rel)
        if commit and recorded_commit is None:
            recorded_commit = commit
        if etag is None:
            etag = _sha256(src)
            digest_pairs.append((rel, etag))
        link_or_copy(src, dest / rel)
        file_meta[rel] = {"size": f["size"], "etag": etag}

    commit_sha = recorded_commit
    if commit_sha is None:
        # Any file without recorded metadata was hashed above; hash the rest
        # so the synthesised commit covers the whole model.
        have = {p for p, _ in digest_pairs}
        for f in files:
            if f["path"] not in have:
                digest_pairs.append((f["path"], _sha256(root / f["path"])))
        joined = "".join(f"{p}:{d}" for p, d in sorted(digest_pairs))
        commit_sha = hashlib.sha1(joined.encode()).hexdigest()

    return {"commit_sha": commit_sha, "file_meta": file_meta}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def ollama_manifest(root: Path, model_id: str, tag: str) -> Path:
    """The manifest for *model_id*:*tag* under an ``~/.ollama/models`` copy.

    Ollama namespaces a bare name under ``library/``, which
    ``mirror_ollama._model_id`` strips again when serving.
    """
    manifests = root / "manifests"
    name = model_id if "/" in model_id else f"library/{model_id}"
    for registry in sorted(p for p in manifests.iterdir() if p.is_dir()):
        candidate = registry / Path(name) / tag
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no manifest for {model_id}:{tag} under {manifests}")


def ollama_layers(manifest_path: Path) -> list[dict]:
    """Every blob the manifest references, config included."""
    data = json.loads(manifest_path.read_text())
    layers = list(data.get("layers") or [])
    if data.get("config"):
        layers.append(data["config"])
    return [
        {"path": layer["digest"], "size": int(layer.get("size") or 0)}
        for layer in layers
        if layer.get("digest")
    ]


def ollama_import(
    root: Path, dest: Path, manifest_path: Path, layers: list[dict],
) -> dict:
    """Link the manifest and its blobs into *dest*, verifying each digest.

    Ollama stores blobs as ``sha256-<hex>``; the mirror serves them as
    ``sha256_<hex>``.
    """
    _reject_symlink(manifest_path, root)
    dest.mkdir(parents=True, exist_ok=True)
    for layer in layers:
        digest = layer["path"]
        src = root / "blobs" / digest.replace(":", "-")
        _reject_symlink(src, root)
        if not src.is_file():
            raise FileNotFoundError(f"manifest references a missing blob: {digest}")
        actual = _sha256(src)
        if f"sha256:{actual}" != digest:
            raise ValueError(f"blob does not match its digest: {digest}")
        link_or_copy(src, dest / digest.replace(":", "_"))
    link_or_copy(manifest_path, dest / "manifest.json")
    return {"commit_sha": None, "file_meta": {}}

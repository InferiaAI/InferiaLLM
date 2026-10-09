"""Every deploy pre-warms, so a cached model must not be re-listed upstream.

Without the skip, a deploy of a cached model asks the origin for the file list
on every attempt. With no egress that call fails and flips a working row to
``error``, after which the mirror is bypassed and the deploy goes to origin.

The skip is guarded on the files actually being there, so a row that reads
cached with its directory gone still recovers.
"""
from __future__ import annotations

import pytest

from orchestration.models.model_cache.downloader import DownloadManager
from orchestration.models.model_cache.paths import CachePaths

from .test_downloader import FakeRepo


def _manager(tmp_path, listed: list):
    """A manager whose list function records that it was called."""
    calls: list = []

    async def list_fn(model_id, revision):
        calls.append((model_id, revision))
        return listed

    async def file_fn(model_id, revision, path, on_bytes):
        return None

    dm = DownloadManager(
        repo=FakeRepo(),
        paths=CachePaths(str(tmp_path)),
        fetch_list=list_fn,
        fetch_file=file_fn,
    )
    return dm, calls


async def _mark_cached(dm, *, source, model_id, revision):
    row = await dm.repo.upsert(source=source, model_id=model_id, revision=revision)
    await dm.repo.set_status(row["id"], "cached")
    return row


@pytest.mark.asyncio
class TestAHuggingFaceRowThatIsCached:

    async def test_a_cached_row_with_files_is_not_re_listed(self, tmp_path):
        dm, calls = _manager(tmp_path, [])
        await _mark_cached(dm, source="hf", model_id="meta/llama", revision="main")
        d = dm.paths.hf_dir("meta/llama", "main")
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

        assert calls == [], "the origin was contacted for a model already on disk"

    async def test_an_unreachable_origin_no_longer_breaks_it(self, tmp_path):
        """The actual failure: with no egress the listing raises, and the row
        went from cached to error with the files still sitting on disk.
        """
        dm, _ = _manager(tmp_path, [])

        async def unreachable(model_id, revision):
            raise OSError("no route to host")

        dm._fetch_list = unreachable
        row = await _mark_cached(dm, source="hf", model_id="meta/llama", revision="main")
        d = dm.paths.hf_dir("meta/llama", "main")
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

        assert dm.repo._rows[row["id"]]["status"] == "cached"

    async def test_a_cached_row_whose_files_are_gone_still_fetches(self, tmp_path):
        """Otherwise the row can never recover from a deleted directory."""
        dm, calls = _manager(tmp_path, [])
        await _mark_cached(dm, source="hf", model_id="meta/llama", revision="main")

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

        assert calls == [("meta/llama", "main")]

    async def test_an_empty_directory_does_not_count(self, tmp_path):
        dm, calls = _manager(tmp_path, [])
        await _mark_cached(dm, source="hf", model_id="meta/llama", revision="main")
        dm.paths.hf_dir("meta/llama", "main").mkdir(parents=True)

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

        assert calls == [("meta/llama", "main")]


@pytest.mark.asyncio
class TestAnUnreadableCacheDirectory:
    """is_dir and iterdir raise PermissionError rather than returning False —
    EACCES is not in pathlib's ignored errnos."""

    async def test_it_does_not_escape_prewarm(self, tmp_path):
        """prewarm promises never to propagate; it runs as a detached task."""
        dm, _ = _manager(tmp_path, [])

        def unreadable(*_):
            raise PermissionError(13, "Permission denied")

        dm._files_present = unreadable
        await _mark_cached(dm, source="hf", model_id="meta/llama", revision="main")

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

    async def test_the_row_is_marked_error(self, tmp_path):
        """Accurate: the mirror could not have read those files either."""
        dm, _ = _manager(tmp_path, [])

        def unreadable(*_):
            raise PermissionError(13, "Permission denied")

        dm._files_present = unreadable
        row = await _mark_cached(dm, source="hf", model_id="meta/llama", revision="main")

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

        assert dm.repo._rows[row["id"]]["status"] == "error"


@pytest.mark.asyncio
class TestARowThatIsNotCached:

    @pytest.mark.parametrize("status", ["pending", "downloading", "error"])
    async def test_it_fetches_whatever_is_on_disk(self, tmp_path, status):
        dm, calls = _manager(tmp_path, [])
        row = await dm.repo.upsert(source="hf", model_id="meta/llama", revision="main")
        await dm.repo.set_status(row["id"], status)
        d = dm.paths.hf_dir("meta/llama", "main")
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")

        await dm.prewarm(source="hf", model_id="meta/llama", revision="main")

        assert calls == [("meta/llama", "main")]


class TestOllama:
    """The mirror reads manifest.json first, so blobs alone cannot be served."""

    def test_a_manifest_counts_as_present(self, tmp_path):
        dm, _ = _manager(tmp_path, [])
        d = dm.paths.ollama_dir("qwen2", "0.5b")
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}")

        assert dm._files_present("ollama", "qwen2", "0.5b") is True

    def test_blobs_without_a_manifest_do_not(self, tmp_path):
        dm, _ = _manager(tmp_path, [])
        d = dm.paths.ollama_dir("qwen2", "0.5b")
        d.mkdir(parents=True)
        (d / "sha256_abc").write_text("blob")

        assert dm._files_present("ollama", "qwen2", "0.5b") is False


class TestTheGuardItself:

    def test_an_unknown_source_is_never_present(self, tmp_path):
        dm, _ = _manager(tmp_path, [])
        assert dm._files_present("oci", "m", "main") is False

    def test_no_paths_configured_is_never_present(self):
        dm = DownloadManager(repo=FakeRepo(), paths=None)
        assert dm._files_present("hf", "m", "main") is False

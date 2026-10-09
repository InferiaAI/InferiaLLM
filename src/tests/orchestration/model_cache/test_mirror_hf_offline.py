"""The mirror must answer HuggingFace metadata with no internet access.

huggingface_hub refuses a file whose resolve HEAD carries no X-Repo-Commit,
and snapshot_download calls the revision endpoint before anything else. Both
used to come from huggingface.co, so a model sitting complete on disk was
unusable on a disconnected network.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from orchestration.models.model_cache import deps, mirror_hf
from orchestration.models.model_cache import paths as paths_mod

pytestmark = pytest.mark.asyncio

COMMIT = "0123456789abcdef0123456789abcdef0123abcd"
REPO = "meta/llama-3"
META = {
    "config.json": {"size": 79, "etag": "etag-config"},
    "model.safetensors": {"size": 20000000, "etag": "etag-weights"},
}


class _NoEgress:
    """Any upstream call is a failure of the thing under test."""

    def stream(self, *a, **kw):
        raise AssertionError(f"upstream was contacted: {a[:2]}")


class _Repo:
    def __init__(self, row):
        self._row = row

    async def get_by_key(self, *, source, model_id, revision):
        r = self._row
        if r and r["source"] == source and r["model_id"] == model_id and r["revision"] == revision:
            return r
        return None

    async def touch_by_key(self, *, source, model_id, revision="main"):
        return None

    async def get_by_commit(self, *, source, model_id, commit_sha):
        r = self._row
        if r and r["source"] == source and r["model_id"] == model_id and r["commit_sha"] == commit_sha:
            return r
        return None


def _row(**over):
    base = {
        "id": "1", "source": "hf", "model_id": REPO, "revision": "main",
        "status": "cached", "commit_sha": COMMIT, "file_meta": META,
    }
    base.update(over)
    return base


@pytest.fixture
def client(tmp_path, request):
    row = getattr(request, "param", None) or _row()
    deps._reset()
    deps.configure(
        repo=_Repo(row),
        paths=paths_mod.CachePaths(str(tmp_path)),
        http_client=_NoEgress(),
        settings=None,
    )
    app = FastAPI()
    app.include_router(mirror_hf.router)
    yield httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ), tmp_path, row
    deps._reset()


class TestTheMetadataHead:

    async def test_it_carries_the_commit_and_etag(self, client):
        c, _, _ = client
        r = await c.head(f"/hf/{REPO}/resolve/main/config.json")

        assert r.status_code == 200
        assert r.headers["x-repo-commit"] == COMMIT
        assert r.headers["etag"] == "etag-config"
        assert r.headers["content-length"] == "79"

    async def test_a_commit_revision_resolves_too(self, client):
        """snapshot_download asks by commit, not by branch."""
        c, _, _ = client
        r = await c.head(f"/hf/{REPO}/resolve/{COMMIT}/model.safetensors")

        assert r.status_code == 200
        assert r.headers["x-repo-commit"] == COMMIT
        assert r.headers["content-length"] == "20000000"


class TestTheApiEndpoints:

    async def test_the_revision_endpoint_returns_the_commit(self, client):
        c, _, _ = client
        r = await c.get(f"/hf/api/models/{REPO}/revision/main")

        assert r.status_code == 200
        body = r.json()
        assert body["sha"] == COMMIT
        assert {s["rfilename"] for s in body["siblings"]} == set(META)

    async def test_the_tree_endpoint_lists_every_file(self, client):
        c, _, _ = client
        r = await c.get(f"/hf/api/models/{REPO}/tree/{COMMIT}")

        assert r.status_code == 200
        entries = {e["path"]: e for e in r.json()}
        assert set(entries) == set(META)
        assert entries["config.json"]["size"] == 79

    async def test_weights_are_marked_as_lfs(self, client):
        """huggingface_hub decides how to fetch a file from this."""
        c, _, _ = client
        entries = {e["path"]: e for e in (
            await c.get(f"/hf/api/models/{REPO}/tree/main")).json()}

        assert "lfs" in entries["model.safetensors"]
        assert "lfs" not in entries["config.json"]


class TestResolvingTheFileItself:

    async def test_a_commit_revision_serves_the_cached_folder(self, client):
        """The pre-warm writes under the cached revision, not the commit."""
        c, tmp_path, _ = client
        d = paths_mod.CachePaths(str(tmp_path)).hf_dir(REPO, "main")
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")

        r = await c.get(f"/hf/{REPO}/resolve/{COMMIT}/config.json")

        assert r.status_code == 200
        assert r.text == "{}"


class TestWhenTheRowCannotAnswer:
    """Anything missing must leave the existing upstream path alone, so a
    connected install keeps working exactly as before."""

    @pytest.mark.parametrize("client", [_row(status="downloading")], indirect=True)
    async def test_a_row_that_is_not_cached_falls_through(self, client):
        c, _, _ = client
        with pytest.raises(AssertionError, match="upstream was contacted"):
            await c.get(f"/hf/api/models/{REPO}/revision/main")

    @pytest.mark.parametrize("client", [_row(commit_sha=None)], indirect=True)
    async def test_a_row_without_a_commit_falls_through(self, client):
        c, _, _ = client
        with pytest.raises(AssertionError, match="upstream was contacted"):
            await c.get(f"/hf/api/models/{REPO}/revision/main")

    async def test_a_file_not_in_the_row_is_a_definite_404(self, client):
        """The row lists every file, so absence is authoritative. A 200
        without a commit makes file_exists() answer True for what is not
        there, and hf_hub_download raise a transport error instead.
        """
        c, _, _ = client
        r = await c.head(f"/hf/{REPO}/resolve/main/not-in-the-row.json")

        assert r.status_code == 404
        assert r.headers["x-error-code"] == "EntryNotFound"


class TestTheRepoLevelEndpoint:
    """HfFileSystem.ls calls /api/models/{repo} with no revision, and vLLM's
    download path reaches it first."""

    async def test_it_answers_as_main(self, client):
        c, _, _ = client
        r = await c.get(f"/hf/api/models/{REPO}")

        assert r.status_code == 200
        assert r.json()["sha"] == COMMIT

    async def test_it_does_not_shadow_the_revision_route(self, client):
        c, _, _ = client
        r = await c.get(f"/hf/api/models/{REPO}/revision/main")

        assert r.status_code == 200
        assert r.json()["sha"] == COMMIT

    async def test_another_repo_falls_through(self, client):
        c, _, _ = client
        with pytest.raises(AssertionError, match="upstream was contacted"):
            await c.get("/hf/api/models/someone/else/revision/main")

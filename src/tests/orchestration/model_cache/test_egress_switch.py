"""INFERIA_MODEL_EGRESS=false declares an install with no route to origin.

Every path that would otherwise reach huggingface.co or registry.ollama.ai has
to fail at once instead of hanging until a timeout, and the one path that can
still fill the cache — import — has to keep working. The upstream client here
raises on use, so any attempt to leave the box fails the test.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from orchestration.config import Settings
from orchestration.models.model_cache import api, deps, mirror_hf, mirror_ollama
from orchestration.models.model_cache import paths as paths_mod
from orchestration.models.model_deployment import mirror_decision

REPO = "meta/llama-3"
COMMIT = "0123456789abcdef0123456789abcdef0123abcd"


class _NoEgress:
    """Any upstream call is a failure of the thing under test."""

    def stream(self, *a, **kw):
        raise AssertionError(f"upstream was contacted: {a[:2]}")


class _Repo:
    def __init__(self, row=None):
        self._row = row

    async def is_mirrored(self, *, source, model_id):
        r = self._row
        return bool(r) and (r["source"], r["model_id"]) == (source, model_id) and (
            r["status"] in ("cached", "downloading", "pending")
        )

    async def get_by_key(self, *, source, model_id, revision):
        r = self._row
        if r and (r["source"], r["model_id"], r["revision"]) == (
            source, model_id, revision
        ):
            return r
        return None

    async def get_by_commit(self, *, source, model_id, commit_sha):
        return None

    async def touch_by_key(self, *, source, model_id, revision="main"):
        return None


class _Downloader:
    def __init__(self):
        self.started: list[dict] = []

    def start(self, *, source, model_id, revision="main", engine_hint=None,
              import_from=None):
        self.started.append({"source": source, "model_id": model_id,
                             "import_from": import_from})

    def is_running(self, *a, **kw):
        return False

    async def await_key(self, *, source, model_id, revision="main"):
        return None


def _configure(tmp_path, *, egress=False, row=None):
    deps._reset()
    dl = _Downloader()
    deps.configure(
        repo=_Repo(row),
        paths=paths_mod.CachePaths(str(tmp_path)),
        http_client=_NoEgress(),
        downloader=dl,
        settings=SimpleNamespace(model_egress=egress, hf_token=""),
    )
    return dl


def _client(*routers):
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture(autouse=True)
def _clean():
    yield
    deps._reset()


class TestTheSetting:
    """An empty mirror base leaves the mirror dormant, so declaring no egress
    without one would send every worker to an origin it cannot reach."""

    def test_egress_is_on_by_default(self, monkeypatch):
        monkeypatch.delenv("INFERIA_MODEL_EGRESS", raising=False)
        assert Settings(_env_file=None).model_egress is True

    def test_turning_it_off_without_a_mirror_base_is_refused(self, monkeypatch):
        monkeypatch.setenv("INFERIA_MODEL_EGRESS", "false")
        monkeypatch.setenv("INFERIA_MODEL_MIRROR_BASE", "")
        with pytest.raises(ValueError, match="INFERIA_MODEL_MIRROR_BASE"):
            Settings(_env_file=None)

    def test_turning_it_off_with_a_mirror_base_is_accepted(self, monkeypatch):
        monkeypatch.setenv("INFERIA_MODEL_EGRESS", "false")
        monkeypatch.setenv("INFERIA_MODEL_MIRROR_BASE", "http://cp:8000/api")
        assert Settings(_env_file=None).model_egress is False


class TestAddingAModel:
    pytestmark = pytest.mark.asyncio

    async def test_an_add_is_refused_and_points_at_import(self, tmp_path):
        dl = _configure(tmp_path)
        async with _client(api.router) as c:
            r = await c.post("/v1/models", json={"source": "hf", "model_id": REPO})

        assert r.status_code == 403
        assert "/v1/models/import" in r.json()["detail"]
        assert dl.started == []

    async def test_an_import_still_works(self, tmp_path):
        dl = _configure(tmp_path)
        (paths_mod.CachePaths(str(tmp_path)).import_root() / "staged").mkdir(
            parents=True
        )
        async with _client(api.router) as c:
            r = await c.post(
                "/v1/models/import",
                json={"format": "hf", "from": "staged", "model_id": REPO},
            )

        assert r.status_code == 202
        assert dl.started[0]["import_from"] == "staged"

    async def test_an_add_still_works_with_egress(self, tmp_path):
        dl = _configure(tmp_path, egress=True)
        async with _client(api.router) as c:
            r = await c.post("/v1/models", json={"source": "hf", "model_id": REPO})

        assert r.status_code == 202
        assert dl.started[0]["model_id"] == REPO


class TestTheHuggingFaceMirror:
    pytestmark = pytest.mark.asyncio

    async def test_an_uncached_repo_is_a_definite_404(self, tmp_path):
        _configure(tmp_path)
        async with _client(mirror_hf.router) as c:
            r = await c.head(f"/hf/{REPO}/resolve/main/config.json")

        assert r.status_code == 404
        assert r.headers["x-error-code"] == "RepoNotFound"

    async def test_a_file_on_disk_still_reports_its_size(self, tmp_path):
        """A row cached before the box was disconnected carries no commit, so
        the metadata reply falls back to what the directory holds."""
        _configure(tmp_path, row={
            "source": "hf", "model_id": REPO, "revision": "main",
            "status": "cached", "commit_sha": None, "file_meta": {},
        })
        d = paths_mod.CachePaths(str(tmp_path)).hf_dir(REPO, "main")
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        async with _client(mirror_hf.router) as c:
            r = await c.head(f"/hf/{REPO}/resolve/main/config.json")

        assert r.status_code == 200
        assert r.headers["content-length"] == "2"

    async def test_the_api_proxy_404s_instead_of_reaching_out(self, tmp_path):
        _configure(tmp_path)
        async with _client(mirror_hf.router) as c:
            revision = await c.get(f"/hf/api/models/{REPO}/revision/main")
            listing = await c.get("/hf/api/models?search=llama")

        assert revision.status_code == 404
        assert listing.status_code == 404

    async def test_a_download_miss_404s(self, tmp_path):
        _configure(tmp_path)
        async with _client(mirror_hf.router) as c:
            r = await c.get(f"/hf/{REPO}/resolve/main/model.safetensors")

        assert r.status_code == 404

    async def test_an_imported_model_is_still_served(self, tmp_path):
        row = {
            "source": "hf", "model_id": REPO, "revision": "main",
            "status": "cached", "commit_sha": COMMIT,
            "file_meta": {"config.json": {"size": 2, "etag": "e1"}},
        }
        _configure(tmp_path, row=row)
        async with _client(mirror_hf.router) as c:
            head = await c.head(f"/hf/{REPO}/resolve/main/config.json")
            info = await c.get(f"/hf/api/models/{REPO}/revision/main")

        assert head.headers["x-repo-commit"] == COMMIT
        assert json.loads(info.content)["sha"] == COMMIT


# The mirror serves only models this install chose to cache.
_QWEN2 = {"source": "ollama", "model_id": "qwen2", "revision": "0.5b",
          "status": "cached"}


class TestTheOllamaMirror:
    pytestmark = pytest.mark.asyncio

    async def test_a_manifest_miss_404s(self, tmp_path):
        _configure(tmp_path, row=_QWEN2)
        async with _client(mirror_ollama.router) as c:
            get = await c.get("/v2/library/qwen2/manifests/0.5b")
            head = await c.head("/v2/library/qwen2/manifests/0.5b")

        assert get.status_code == 404
        assert head.status_code == 404

    async def test_a_blob_miss_404s(self, tmp_path):
        _configure(tmp_path, row=_QWEN2)
        digest = "sha256:" + "f" * 64
        async with _client(mirror_ollama.router) as c:
            get = await c.get(f"/v2/library/qwen2/blobs/{digest}?download=1")
            head = await c.head(f"/v2/library/qwen2/blobs/{digest}")

        assert get.status_code == 404
        assert head.status_code == 404

    async def test_an_imported_manifest_is_still_served(self, tmp_path):
        _configure(tmp_path, row=_QWEN2)
        d = paths_mod.CachePaths(str(tmp_path)).ollama_dir("qwen2", "0.5b")
        d.mkdir(parents=True)
        (d / "manifest.json").write_text('{"layers": []}')
        async with _client(mirror_ollama.router) as c:
            r = await c.get("/v2/library/qwen2/manifests/0.5b")

        assert r.status_code == 200
        assert json.loads(r.content) == {"layers": []}


class TestRefusingAnUnloadableDeploy:
    """The gate runs before the deployment row is created, so an operator who
    forgot to import sees a 409 rather than a node that boots and then fails."""

    pytestmark = pytest.mark.asyncio

    @staticmethod
    def _cached():
        return {"source": "hf", "model_id": REPO, "revision": "main",
                "status": "cached", "commit_sha": COMMIT}

    async def _blocked(self, tmp_path, *, egress=False, row=None, recipe="vllm",
                       uri=REPO, on_disk=True):
        _configure(tmp_path, egress=egress)
        paths = paths_mod.CachePaths(str(tmp_path))
        if on_disk and row:
            if row["source"] == "hf":
                d = paths.hf_dir(row["model_id"], row["revision"])
                d.mkdir(parents=True)
                (d / "config.json").write_text("{}")
            else:
                d = paths.ollama_dir(row["model_id"], row["revision"])
                d.mkdir(parents=True)
                (d / "manifest.json").write_text("{}")
        return await mirror_decision.blocking_uncached_model(
            recipe=recipe, artifact_uri=uri, cache_repo=_Repo(row),
            cache_paths=paths,
        )

    async def test_a_model_not_in_the_cache_blocks(self, tmp_path):
        assert await self._blocked(tmp_path) == REPO

    async def test_an_import_still_in_flight_passes(self, tmp_path):
        """The mirror joins it through await_key, so refusing here would
        refuse a deploy that would have worked."""
        row = {**self._cached(), "status": "downloading"}
        assert await self._blocked(tmp_path, row=row) is None

    async def test_a_failed_import_blocks(self, tmp_path):
        row = {**self._cached(), "status": "error"}
        assert await self._blocked(tmp_path, row=row) == REPO

    async def test_an_imported_model_passes(self, tmp_path):
        assert await self._blocked(tmp_path, row=self._cached()) is None

    async def test_an_ollama_tag_is_keyed_the_same_way_as_the_pre_warm(
        self, tmp_path
    ):
        row = {"source": "ollama", "model_id": "qwen2", "revision": "0.5b",
               "status": "cached"}
        assert await self._blocked(
            tmp_path, row=row, recipe="ollama", uri="qwen2:0.5b"
        ) is None

    async def test_an_engine_that_does_not_use_the_cache_is_not_gated(
        self, tmp_path
    ):
        """sglang loads its own way, so it is neither pre-warmed nor mirrored."""
        assert await self._blocked(tmp_path, recipe="sglang") is None

    async def test_a_model_downloaded_while_connected_blocks(self, tmp_path):
        """Only an import records commit_sha, and the HF mirror can answer
        offline from nothing else. Without this the deploy is accepted and
        then fails at load, which is what the gate exists to prevent.
        """
        row = {**self._cached(), "commit_sha": None}
        assert await self._blocked(tmp_path, row=row) == REPO

    async def test_an_import_in_flight_has_no_commit_yet_and_passes(
        self, tmp_path
    ):
        """_run_import records the commit just before flipping to cached, so a
        downloading row legitimately has none."""
        row = {**self._cached(), "status": "downloading", "commit_sha": None}
        assert await self._blocked(tmp_path, row=row, on_disk=False) is None

    async def test_an_ollama_row_needs_no_commit(self, tmp_path):
        """/v2 serves from the manifest on disk, not from recorded metadata."""
        row = {"source": "ollama", "model_id": "qwen2", "revision": "0.5b",
               "status": "cached"}
        assert await self._blocked(
            tmp_path, row=row, recipe="ollama", uri="qwen2:0.5b"
        ) is None

    async def test_a_cached_row_whose_files_were_evicted_blocks(self, tmp_path):
        """Eviction leaves the row reading cached, so the status alone would
        wave through a deploy with nothing for the mirror to serve."""
        assert await self._blocked(
            tmp_path, row=self._cached(), on_disk=False
        ) == REPO

    async def test_an_import_in_flight_is_not_held_to_its_files(self, tmp_path):
        row = {**self._cached(), "status": "downloading"}
        assert await self._blocked(tmp_path, row=row, on_disk=False) is None

    async def test_nothing_is_gated_while_egress_is_allowed(self, tmp_path):
        assert await self._blocked(tmp_path, egress=True) is None


class TestTheDownloaderBackstop:
    """The deploy pre-warm calls start() for every deploy, so the cache being
    import-only has to hold there too, not just on the API."""

    pytestmark = pytest.mark.asyncio

    @staticmethod
    def _manager(tmp_path, *, egress, status="cached"):
        from orchestration.models.model_cache.downloader import DownloadManager

        class _R:
            def __init__(self):
                self.status: tuple | None = None

            async def upsert(self, *, source, model_id, revision, engine_hint=None):
                return {"id": "c1", "status": status}

            async def set_status(self, cid, st, msg=None):
                self.status = (st, msg)

        repo = _R()
        return DownloadManager(
            repo=repo, paths=paths_mod.CachePaths(str(tmp_path)),
            http_client=_NoEgress(),
            settings=SimpleNamespace(model_egress=egress, hf_token=""),
        ), repo

    async def test_an_evicted_cached_row_errors_instead_of_refetching(self, tmp_path):
        """The row says cached but the files are gone, which is what sent the
        pre-warm back to origin before this guard."""
        dm, repo = self._manager(tmp_path, egress=False)
        await dm.prewarm(source="hf", model_id=REPO)

        assert repo.status[0] == "error"
        assert "import" in repo.status[1]

    async def test_a_cached_row_with_its_files_is_still_a_silent_no_op(
        self, tmp_path
    ):
        dm, repo = self._manager(tmp_path, egress=False)
        d = paths_mod.CachePaths(str(tmp_path)).hf_dir(REPO, "main")
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        await dm.prewarm(source="hf", model_id=REPO)

        assert repo.status is None

    async def test_an_import_is_not_blocked(self, tmp_path):
        dm, repo = self._manager(tmp_path, egress=False)
        await dm.prewarm(source="hf", model_id=REPO, import_from="missing")

        # Fails on the absent staging directory, not on the egress guard.
        assert repo.status[0] == "error"
        assert "staging" in repo.status[1] and "egress" not in repo.status[1]


class TestPreflightWithNoEgress:
    """Every check already fails open on an unreachable registry. With no
    egress that answer is known up front, so it must not cost a timeout."""

    pytestmark = pytest.mark.asyncio

    async def test_the_checks_skip_without_contacting_anything(self, tmp_path):
        import orchestration.models.model_deployment.preflight as pf

        _configure(tmp_path)

        class _Contacted(BaseException):
            """Not an Exception: every check catches that and fails open, so
            an Exception here would be swallowed and prove nothing."""

        def _boom(*a, **kw):
            raise _Contacted("preflight opened an HTTP client")

        original, pf.httpx.AsyncClient = pf.httpx.AsyncClient, _boom
        try:
            assert (await pf.check_model_accessibility(REPO)).skipped is True
            assert (await pf.check_model_format(REPO, "vllm")).skipped is True
            assert (await pf.check_ollama_model_exists("qwen2")).skipped is True
            assert await pf.fetch_hf_model_info(REPO) is None
            assert await pf.fetch_native_max_len(REPO) is None
        finally:
            pf.httpx.AsyncClient = original


class TestTheProviderOriginPreference:
    """Public-cloud VMs normally fetch weights from origin themselves. With no
    egress they cannot, so the preference has to be ignored."""

    pytestmark = pytest.mark.asyncio

    async def _spec_for(self, tmp_path, *, egress):
        _configure(tmp_path, egress=egress)
        row = {"source": "hf", "model_id": REPO, "revision": "main",
               "status": "cached"}
        spec = {"model": {"artifact_uri": REPO}}
        await mirror_decision.resolve_and_apply_mirror(
            spec, recipe="vllm", artifact_uri=REPO,
            mirror_base="http://cp:8000/api", cache_repo=_Repo(row),
            provider="aws",
        )
        return spec

    async def test_the_mirror_is_injected_with_no_egress(self, tmp_path):
        spec = await self._spec_for(tmp_path, egress=False)
        assert spec["env"]["HF_ENDPOINT"] == "http://cp:8000/api/hf"

    async def test_origin_still_wins_when_egress_is_allowed(self, tmp_path):
        spec = await self._spec_for(tmp_path, egress=True)
        assert "env" not in spec


class TestAMixedCaseEngineName:
    """The engine string reaches the cache key and the load spec as sent. An
    unnormalised one keys the wrong source and points the worker at origin."""

    pytestmark = pytest.mark.asyncio

    def test_the_cache_key_matches_the_lowercase_one(self):
        assert mirror_decision.derive_cache_key(
            "Ollama", "qwen2:0.5b"
        ) == mirror_decision.derive_cache_key("ollama", "qwen2:0.5b")

    def test_the_spec_still_gets_the_mirror(self):
        spec = {"model": {"artifact_uri": "qwen2:0.5b"}}
        mirror_decision.apply_mirror_to_spec(
            spec, recipe="Ollama", mirror_base="http://cp:8000/api",
        )
        assert spec["model"]["artifact_uri"].startswith("http://cp:8000/")

    @staticmethod
    async def _gate(row, tmp_path):
        _configure(tmp_path)
        return await mirror_decision.blocking_uncached_model(
            recipe="Ollama", artifact_uri="qwen2:0.5b",
            cache_repo=_Repo(row), cache_paths=None,
        )

    async def test_the_deploy_gate_passes_a_cached_one(self, tmp_path):
        row = {"source": "ollama", "model_id": "qwen2", "revision": "0.5b",
               "status": "cached"}
        assert await self._gate(row, tmp_path) is None

    async def test_the_deploy_gate_blocks_an_uncached_one(self, tmp_path):
        """An unnormalised name falls out of MIRRORED_RECIPES and skips the
        gate entirely, so the deploy is accepted and fails at load."""
        assert await self._gate(None, tmp_path) == "qwen2"


class TestTheGatewayForwardsTheMirrorHeaders:
    """The mount at /api is what the load spec injects as HF_ENDPOINT, and it
    forwards a whitelist. A header missing from it does not reach the engine."""

    def test_entry_not_found_is_forwarded(self):
        from api_gateway.gateway.proxy_routes import _STREAM_FORWARD_HEADERS

        # Without this, huggingface_hub reads the mirror's authoritative 404 as
        # a transport failure and every optional-file probe raises.
        assert "x-error-code" in _STREAM_FORWARD_HEADERS

    def test_the_metadata_headers_are_forwarded(self):
        from api_gateway.gateway.proxy_routes import _STREAM_FORWARD_HEADERS

        assert {"x-repo-commit", "etag", "content-length"} <= _STREAM_FORWARD_HEADERS

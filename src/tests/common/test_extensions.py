"""Installed extensions: found by entry point, mounted on the gateway, migrated with core."""
import pytest

from common import extensions

pytestmark = pytest.mark.asyncio


class _EP:
    def __init__(self, name, target):
        self.name, self._target = name, target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return lambda: self._target


@pytest.fixture
def install(monkeypatch):
    def _install(*eps):
        monkeypatch.setattr(extensions, "entry_points", lambda group: list(eps) if group == extensions.GROUP else [])
        extensions.installed.cache_clear()
    yield _install
    extensions.installed.cache_clear()


class _Ext:
    def __init__(self, fail=False):
        self.fail, self.mounted, self.migrated = fail, [], []

    def mount(self, app):
        if self.fail:
            raise RuntimeError("bug")
        self.mounted.append(app)

    async def migrate(self, dsn):
        if self.fail:
            raise RuntimeError("bug")
        self.migrated.append(dsn)


async def test_with_nothing_installed_nothing_happens(install):
    install()
    extensions.mount_all("app")
    await extensions.migrate_all("postgresql://x")
    assert extensions.installed() == ()


async def test_each_extension_is_mounted_and_migrated(install):
    ext = _Ext()
    install(_EP("one", ext))
    extensions.mount_all("app")
    await extensions.migrate_all("postgresql://x")
    assert (ext.mounted, ext.migrated) == (["app"], ["postgresql://x"])


async def test_a_failing_extension_is_skipped_and_the_others_still_run(install):
    broken, good = _Ext(fail=True), _Ext()
    install(_EP("missing", ImportError("not installed")), _EP("broken", broken), _EP("good", good))
    extensions.mount_all("app")
    await extensions.migrate_all("postgresql://x")
    assert (good.mounted, good.migrated) == (["app"], ["postgresql://x"])


async def test_an_extension_may_provide_only_one_of_the_two(install):
    class OnlyMount:
        def __init__(self):
            self.mounted = []

        def mount(self, app):
            self.mounted.append(app)

    ext = OnlyMount()
    install(_EP("only", ext))
    extensions.mount_all("app")
    await extensions.migrate_all("postgresql://x")
    assert ext.mounted == ["app"]


async def test_migrations_run_core_first_then_extensions(monkeypatch):
    from cli import init

    order = []

    async def core(dsn):
        order.append(("core", dsn))

    async def ext(dsn):
        order.append(("extensions", dsn))

    monkeypatch.setattr(init, "_apply_core_migrations", core)
    monkeypatch.setattr(init, "migrate_all", ext)
    await init._apply_migrations("postgresql://x")
    assert order == [("core", "postgresql://x"), ("extensions", "postgresql://x")]

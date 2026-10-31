"""Extension registry (src/common/extensions/) — plan.md §3 rule 2, interfaces.md §6.

Covers: env-var selection, open-source defaults, fail-fast on bad config,
singleton caching, test overrides, and that the registry itself imports
nothing outside the standard library (core never imports plugin code).
"""

import ast
import asyncio
import pathlib
import sys
import types

import pytest

from common import extensions
from common.extensions import ExtensionConfigError, registry
from common.extensions.protocols import (
    AuditEvent,
    AuditSink,
    QuotaContext,
    QuotaDecision,
    QuotaPolicy,
    TokenVerifier,
    UsageEstimate,
    UsageEvent,
    UsageSink,
)

FAKE_MOD = "tests_fake_ext_plugin"


class FakeAuditSink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class NotASink:
    def something_else(self):
        pass


class SyncEmitSink:
    def emit(self, event):  # not async — must be rejected
        pass


class ExplodingInit:
    def __init__(self):
        raise RuntimeError("boom")

    async def emit(self, event):
        pass


not_a_class = 42


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("INFERIA_EXT_"):
            monkeypatch.delenv(key, raising=False)
    mod = types.ModuleType(FAKE_MOD)
    mod.FakeAuditSink = FakeAuditSink
    mod.NotASink = NotASink
    mod.SyncEmitSink = SyncEmitSink
    mod.ExplodingInit = ExplodingInit
    mod.not_a_class = not_a_class
    mod.ns = types.SimpleNamespace(Nested=FakeAuditSink)
    monkeypatch.setitem(sys.modules, FAKE_MOD, mod)
    extensions.reset()
    yield
    extensions.reset()


# ---------------------------------------------------------------- defaults


def test_builtin_kinds_and_defaults_are_declared():
    kinds = extensions.kinds()
    for kind in ("audit_sink", "usage_sink", "quota_policy", "token_verifier"):
        assert kind in kinds
        assert kinds[kind].default is not None
    # Session 3's hook: reserved, no default until it lands.
    assert "admission_policy" in kinds
    assert kinds["admission_policy"].default is None


def test_env_var_name_is_derived_from_kind():
    assert registry.env_var("audit_sink") == "INFERIA_EXT_AUDIT_SINK"
    assert registry.env_var("token_verifier") == "INFERIA_EXT_TOKEN_VERIFIER"


def test_default_is_used_when_env_unset(monkeypatch):
    extensions.register_kind("demo_sink", AuditSink, default=f"{FAKE_MOD}:FakeAuditSink")
    impl = extensions.get("demo_sink")
    assert isinstance(impl, FakeAuditSink)


def test_get_caches_a_singleton():
    extensions.register_kind("demo_sink", AuditSink, default=f"{FAKE_MOD}:FakeAuditSink")
    assert extensions.get("demo_sink") is extensions.get("demo_sink")


def test_reserved_kind_without_default_or_override_raises():
    with pytest.raises(ExtensionConfigError, match="admission_policy"):
        extensions.get("admission_policy")


def test_unknown_kind_raises():
    with pytest.raises(ExtensionConfigError, match="unknown extension kind"):
        extensions.get("nope")


# ---------------------------------------------------------------- env override


def test_env_override_swaps_in_fake_and_it_is_called(monkeypatch):
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"{FAKE_MOD}:FakeAuditSink")
    sink = extensions.get("audit_sink")
    assert isinstance(sink, FakeAuditSink)
    ev = AuditEvent(action="user.login")
    asyncio.run(sink.emit(ev))
    assert sink.events == [ev]


def test_env_override_accepts_dotted_attribute_path(monkeypatch):
    # packaging "object reference" syntax: module:attr.attr
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"{FAKE_MOD}:ns.Nested")
    assert isinstance(extensions.get("audit_sink"), FakeAuditSink)


def test_env_value_is_stripped(monkeypatch):
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"  {FAKE_MOD}:FakeAuditSink  ")
    assert isinstance(extensions.get("audit_sink"), FakeAuditSink)


@pytest.mark.parametrize(
    "value, fragment",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("no_colon_here", "module:attribute"),
        (":Cls", "module:attribute"),
        ("mod:", "module:attribute"),
        ("a:b:c", "module:attribute"),
        ("bad-name:Cls", "module:attribute"),
        ("definitely_not_a_module_xyz:Cls", "cannot import"),
        (f"{FAKE_MOD}:Missing", "has no attribute"),
        (f"{FAKE_MOD}:not_a_class", "not a class"),
        (f"{FAKE_MOD}:NotASink", "does not implement"),
        (f"{FAKE_MOD}:SyncEmitSink", "must be async"),
        (f"{FAKE_MOD}:ExplodingInit", "failed to instantiate"),
    ],
)
def test_bad_config_fails_fast_with_clear_error(monkeypatch, value, fragment):
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", value)
    with pytest.raises(ExtensionConfigError) as exc:
        extensions.get("audit_sink")
    msg = str(exc.value)
    assert fragment in msg
    assert "INFERIA_EXT_AUDIT_SINK" in msg


def test_bad_config_never_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"{FAKE_MOD}:Missing")
    for _ in range(2):  # not cached as the default after the first failure
        with pytest.raises(ExtensionConfigError):
            extensions.get("audit_sink")


def test_import_error_inside_plugin_is_not_masked(monkeypatch):
    monkeypatch.setitem(sys.modules, "tests_fake_broken_plugin", None)  # import -> ImportError
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", "tests_fake_broken_plugin:X")
    with pytest.raises(ExtensionConfigError, match="cannot import"):
        extensions.get("audit_sink")


# ---------------------------------------------------------------- startup validation


def test_validate_resolves_requested_kinds(monkeypatch):
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SINK", f"{FAKE_MOD}:FakeAuditSink")
    resolved = extensions.validate(["audit_sink"])
    assert isinstance(resolved["audit_sink"], FakeAuditSink)


def test_validate_rejects_unknown_env_kind(monkeypatch):
    monkeypatch.setenv("INFERIA_EXT_AUDIT_SNK", f"{FAKE_MOD}:FakeAuditSink")  # typo
    with pytest.raises(ExtensionConfigError, match="INFERIA_EXT_AUDIT_SNK"):
        extensions.validate([])


def test_validate_checks_overrides_for_kinds_not_requested(monkeypatch):
    # A bad override for a kind this process does not use still fails startup.
    monkeypatch.setenv("INFERIA_EXT_QUOTA_POLICY", f"{FAKE_MOD}:Missing")
    with pytest.raises(ExtensionConfigError, match="INFERIA_EXT_QUOTA_POLICY"):
        extensions.validate([])


def test_validate_skips_reserved_kind_without_override():
    extensions.validate(["admission_policy"])  # no default, no override: fine


def test_register_kind_rejects_duplicates_and_bad_names():
    with pytest.raises(ValueError):
        extensions.register_kind("audit_sink", AuditSink, default=None)
    with pytest.raises(ValueError):
        extensions.register_kind("Bad-Kind", AuditSink, default=None)


def test_register_kind_fills_reserved_default():
    # Session 3 supplies admission_policy's default from its own package.
    extensions.register_kind(
        "admission_policy", AuditSink, default=f"{FAKE_MOD}:FakeAuditSink"
    )
    assert isinstance(extensions.get("admission_policy"), FakeAuditSink)


# ---------------------------------------------------------------- overrides for tests


def test_override_context_manager_restores():
    extensions.register_kind("demo_sink", AuditSink, default=f"{FAKE_MOD}:FakeAuditSink")
    fake = FakeAuditSink()
    with extensions.override("demo_sink", fake):
        assert extensions.get("demo_sink") is fake
    assert extensions.get("demo_sink") is not fake


def test_override_checks_protocol():
    with pytest.raises(ExtensionConfigError):
        with extensions.override("audit_sink", NotASink()):
            pass


# ---------------------------------------------------------------- protocols


def test_protocols_are_runtime_checkable():
    assert isinstance(FakeAuditSink(), AuditSink)
    assert not isinstance(NotASink(), AuditSink)

    class U:
        async def record(self, events, *, db=None): ...

    class Q:
        async def check(self, ctx, estimate, *, db=None): ...

    class T:
        async def verify(self, token, *, db=None): ...

    assert isinstance(U(), UsageSink)
    assert isinstance(Q(), QuotaPolicy)
    assert isinstance(T(), TokenVerifier)


def test_value_objects_defaults():
    ev = UsageEvent(user_id="u", model="m", prompt_tokens=1, completion_tokens=2, total_tokens=3)
    assert ev.schema_version == "1"
    assert len(ev.event_id) == 36 and ev.ts.endswith("Z")
    assert QuotaDecision.allow().allowed is True
    d = QuotaDecision.deny("over", retry_after_s=5)
    assert (d.allowed, d.reason, d.retry_after_s) == (False, "over", 5)
    assert QuotaContext(user_id="u", model="m").org_id is None
    assert UsageEstimate().total_tokens == 0
    a = AuditEvent(action="x")
    assert a.status == "success" and len(a.id) == 36


# ---------------------------------------------------------------- layering


def test_registry_imports_only_stdlib():
    """Core never imports plugin/enterprise code; the registry stays dependency-free.

    Defaults are referenced by dotted string and imported lazily, so even the
    OSS default implementations are not imported by common.extensions itself.
    """
    pkg = pathlib.Path(extensions.__file__).parent
    stdlib = set(sys.stdlib_module_names)
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top in stdlib or name.startswith("common.extensions"), (
                    f"{py.name} imports non-stdlib module {name!r}"
                )


def test_override_for_reserved_kind_fails_until_protocol_registered(monkeypatch):
    monkeypatch.setenv("INFERIA_EXT_ADMISSION_POLICY", f"{FAKE_MOD}:FakeAuditSink")
    with pytest.raises(ExtensionConfigError, match="reserved"):
        extensions.validate([])


def test_register_kind_rejects_malformed_default():
    with pytest.raises(ValueError, match="module:Attr"):
        extensions.register_kind("demo_sink", AuditSink, default="not a spec")


def test_override_unknown_kind_raises():
    with pytest.raises(ExtensionConfigError, match="unknown extension kind"):
        with extensions.override("nope", object()):
            pass


def test_nested_overrides_restore_previous():
    a, b = FakeAuditSink(), FakeAuditSink()
    with extensions.override("audit_sink", a):
        with extensions.override("audit_sink", b):
            assert extensions.get("audit_sink") is b
        assert extensions.get("audit_sink") is a

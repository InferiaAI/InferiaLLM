"""Tests for the AWS instance catalog."""
from dataclasses import FrozenInstanceError

import pytest

from inferia.services.orchestration.services.adapter_engine.adapters.aws.instance_catalog import (
    INSTANCE_CATALOG,
    InstanceType,
    by_class,
    lookup,
)


def test_catalog_is_not_empty():
    assert len(INSTANCE_CATALOG) > 0


def test_every_entry_has_a_valid_class():
    for it in INSTANCE_CATALOG:
        assert it.cls in {"normal_gpu", "heavy_gpu", "cpu"}


def test_every_entry_has_consistent_gpu_fields():
    """gpu_count > 0 ⇔ cls != 'cpu' ⇔ gpu_model is set."""
    for it in INSTANCE_CATALOG:
        if it.cls == "cpu":
            assert it.gpu_count == 0
            assert it.gpu_model is None
            assert it.gpu_ram_gb == 0
        else:
            assert it.gpu_count > 0
            assert it.gpu_model is not None
            assert it.gpu_ram_gb > 0


def test_catalog_includes_all_three_classes():
    classes = {it.cls for it in INSTANCE_CATALOG}
    assert classes == {"normal_gpu", "heavy_gpu", "cpu"}


def test_names_are_unique():
    names = [it.name for it in INSTANCE_CATALOG]
    assert len(names) == len(set(names))


def test_by_class_returns_only_matching_entries():
    cpu_entries = by_class("cpu")
    assert all(it.cls == "cpu" for it in cpu_entries)
    assert cpu_entries  # non-empty


def test_by_class_unknown_returns_empty_list():
    assert by_class("quantum_gpu") == []


def test_lookup_returns_matching_entry():
    sample = INSTANCE_CATALOG[0]
    assert lookup(sample.name) == sample


def test_lookup_unknown_returns_none():
    assert lookup("z9.imaginary") is None


def test_normal_gpu_default_set_present():
    """The default tier must include g5.xlarge and g6.xlarge."""
    names = {it.name for it in by_class("normal_gpu")}
    assert "g5.xlarge" in names
    assert "g6.xlarge" in names


def test_cpu_default_set_present():
    """CPU tier must include common c6i + m6i sizes."""
    names = {it.name for it in by_class("cpu")}
    assert "c6i.xlarge" in names
    assert "m6i.xlarge" in names


def test_heavy_gpu_default_set_present():
    """Heavy GPU tier must include at least one p-family instance."""
    names = {it.name for it in by_class("heavy_gpu")}
    assert any(n.startswith("p4d.") or n.startswith("p5.") for n in names)


def test_instance_type_is_frozen():
    """InstanceType records are immutable."""
    sample = INSTANCE_CATALOG[0]
    with pytest.raises(FrozenInstanceError):
        sample.name = "z.0"  # type: ignore[misc]


def test_approx_usd_per_hour_positive():
    """All entries have a positive approximate hourly price."""
    for it in INSTANCE_CATALOG:
        assert it.approx_usd_per_hour > 0

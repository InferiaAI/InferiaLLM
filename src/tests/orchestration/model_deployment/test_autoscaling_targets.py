import json

import pytest
from pydantic import ValidationError

from orchestration.models.model_deployment.deployment_server import (
    AutoscalingTargets,
    DeployModelRequest,
    UpdateDeploymentRequest,
    autoscaling_from_configuration,
)


class TestWhatReachesTheAdapter:
    """The k8s adapter reads `autoscale_`-prefixed keys out of configuration,
    so only that spelling gets picked up."""

    def test_the_keys_are_the_ones_the_adapter_reads(self):
        got = AutoscalingTargets(
            p95_seconds=2, max_replicas=5,
            in_flight_per_replica=4, min_samples=20,
        ).as_configuration()
        assert got == {
            "autoscale_p95_seconds": 2,
            "autoscale_max_replicas": 5,
            "autoscale_in_flight_per_replica": 4,
            "autoscale_min_samples": 20,
        }

    def test_unset_values_are_omitted_so_the_defaults_still_apply(self):
        got = AutoscalingTargets(p95_seconds=2).as_configuration()
        assert got == {"autoscale_p95_seconds": 2}

    def test_nothing_set_produces_nothing(self):
        assert AutoscalingTargets().as_configuration() == {}


class TestValidation:

    @pytest.mark.parametrize(
        "field,value",
        [
            ("p95_seconds", 0), ("p95_seconds", 301),
            ("max_replicas", 0), ("max_replicas", 51),
            ("in_flight_per_replica", 0), ("in_flight_per_replica", 101),
            ("min_samples", 0), ("min_samples", 1001),
        ],
    )
    def test_out_of_range_is_rejected(self, field, value):
        with pytest.raises(ValidationError):
            AutoscalingTargets(**{field: value})

    @pytest.mark.parametrize(
        "field,value",
        [
            ("p95_seconds", 1), ("p95_seconds", 300),
            ("max_replicas", 1), ("max_replicas", 50),
            ("in_flight_per_replica", 1), ("in_flight_per_replica", 100),
            ("min_samples", 1), ("min_samples", 1000),
        ],
    )
    def test_the_bounds_themselves_are_accepted(self, field, value):
        assert getattr(AutoscalingTargets(**{field: value}), field) == value


class TestReadingItBack:

    def test_a_stored_configuration_reports_its_targets(self):
        config = {
            "api_key": "sk-secret",
            "autoscale_p95_seconds": 2,
            "autoscale_max_replicas": 5,
        }
        assert autoscaling_from_configuration(config) == {
            "p95_seconds": 2, "max_replicas": 5,
        }

    def test_a_json_string_is_accepted(self):
        got = autoscaling_from_configuration(json.dumps({"autoscale_min_samples": 7}))
        assert got == {"min_samples": 7}

    def test_a_configuration_without_targets_reports_none(self):
        assert autoscaling_from_configuration({"api_key": "sk-secret"}) == {}

    @pytest.mark.parametrize("value", [None, "", "not json", 42, []])
    def test_anything_unusable_reports_none(self, value):
        assert autoscaling_from_configuration(value) == {}

    def test_it_does_not_leak_other_configuration(self):
        got = autoscaling_from_configuration({"api_key": "sk-secret", "env": {"A": "1"}})
        assert "api_key" not in got and "env" not in got


class TestTheRequests:

    def test_create_defaults_to_none_so_nothing_is_written(self):
        req = DeployModelRequest(
            model_name="m", model_version="1", replicas=1, gpu_per_replica=1,
        )
        assert req.autoscaling is None

    def test_create_accepts_targets(self):
        req = DeployModelRequest(
            model_name="m", model_version="1", replicas=1, gpu_per_replica=1,
            autoscaling={"p95_seconds": 2},
        )
        assert req.autoscaling.as_configuration() == {"autoscale_p95_seconds": 2}

    def test_update_accepts_targets_on_their_own(self):
        req = UpdateDeploymentRequest(autoscaling={"max_replicas": 4})
        assert req.configuration is None
        assert req.autoscaling.as_configuration() == {"autoscale_max_replicas": 4}

    def test_an_invalid_target_fails_the_whole_request(self):
        with pytest.raises(ValidationError):
            UpdateDeploymentRequest(autoscaling={"max_replicas": 999})


class TestUpdateWithBothFields:
    """The dashboard sends a whole configuration on save. The targets have to
    survive that, and a merge before the replace would be discarded."""

    def test_the_two_can_be_sent_together(self):
        req = UpdateDeploymentRequest(
            configuration={"api_key": "sk-secret"}, autoscaling={"p95_seconds": 3},
        )
        folded = {**req.configuration, **req.autoscaling.as_configuration()}
        assert folded == {"api_key": "sk-secret", "autoscale_p95_seconds": 3}

    def test_the_targets_win_over_a_stale_copy_in_the_configuration(self):
        req = UpdateDeploymentRequest(
            configuration={"autoscale_p95_seconds": 99},
            autoscaling={"p95_seconds": 3},
        )
        folded = {**req.configuration, **req.autoscaling.as_configuration()}
        assert folded["autoscale_p95_seconds"] == 3


class TestItReachesKeda:
    """Two modules agree on these key names and nothing else checks that."""

    def _scaled_object(self, targets):
        from orchestration.models.model_deployment.direct_provision import (
            _build_metadata,
        )
        from providers.k8s.k8s_adapter import _scaled_object_body

        row = {
            "deployment_id": "d-1",
            "auto_replica_enabled": True,
            "configuration": json.dumps(
                {"api_key": "sk-secret", **targets.as_configuration()}
            ),
        }
        metadata = _build_metadata(row)
        assert metadata["autoscale"] is True
        return _scaled_object_body("eng-1", "d-1", metadata)

    def test_the_latency_target_lands_in_the_scaler_query(self):
        body = self._scaled_object(AutoscalingTargets(p95_seconds=17))
        query = body["spec"]["triggers"][0]["metadata"]["query"]
        # The target gates the histogram_quantile term; the sample floor uses
        # the same `> bool` spelling further along.
        assert "histogram_quantile" in query
        assert query.index("> bool 17)") > query.index("histogram_quantile")

    def test_the_replica_ceiling_lands_on_the_scaled_object(self):
        body = self._scaled_object(AutoscalingTargets(max_replicas=7))
        assert body["spec"]["maxReplicaCount"] == 7

    def test_in_flight_per_replica_becomes_the_threshold(self):
        body = self._scaled_object(AutoscalingTargets(in_flight_per_replica=9))
        assert body["spec"]["triggers"][0]["metadata"]["threshold"] == "9"

    def test_the_sample_floor_lands_in_the_scaler_query(self):
        body = self._scaled_object(AutoscalingTargets(min_samples=25))
        query = body["spec"]["triggers"][0]["metadata"]["query"]
        assert "> bool 25)" in query

    def test_targets_left_unset_fall_back_to_the_adapter_defaults(self):
        from providers.k8s import k8s_adapter

        body = self._scaled_object(AutoscalingTargets())
        assert body["spec"]["maxReplicaCount"] == k8s_adapter._DEFAULT_MAX_REPLICAS
        query = body["spec"]["triggers"][0]["metadata"]["query"]
        assert f"> bool {k8s_adapter._DEFAULT_P95_TARGET_SECONDS})" in query

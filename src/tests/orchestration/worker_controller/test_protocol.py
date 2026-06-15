"""Unit tests for the worker control-channel protocol envelopes.

Covers the new shell/logs stream multiplexing types end-to-end:

* round-trip through JSON (the wire format) — what the CP/worker actually
  exchange,
* defaults for optional fields,
* size + character edge cases (empty, large, NULs in shell output, UTF-8
  multibyte, ANSI control chars),
* MessageType Literal includes every new envelope type so the channel read
  loop can dispatch by name without a follow-up Pydantic error.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from orchestration.workers.worker_controller.protocol import (
    Envelope,
    LogsCloseBody,
    LogsEndBody,
    LogsLineBody,
    LogsOpenBody,
    MessageType,
    ShellCloseBody,
    ShellErrorBody,
    ShellExitBody,
    ShellInputBody,
    ShellOpenBody,
    ShellOutputBody,
    ShellResizeBody,
)


# --- MessageType registration ---------------------------------------------


def test_message_type_includes_all_shell_logs_envelopes():
    """Channel read loop dispatches by envelope ``type`` — every new body
    type must have a matching Literal entry, otherwise Envelope construction
    raises ValidationError instead of failing gracefully."""
    expected = {
        "ShellOpen", "ShellInput", "ShellResize", "ShellClose",
        "ShellOutput", "ShellExit", "ShellError",
        "LogsOpen", "LogsClose", "LogsLine", "LogsEnd",
    }
    # Pydantic stores Literal members on the type's __args__.
    actual = set(MessageType.__args__)
    assert expected <= actual, f"missing envelope types: {expected - actual}"


# --- ShellOpen / ShellInput / ShellResize / ShellClose --------------------


def test_shell_open_minimal_payload_has_safe_defaults():
    body = ShellOpenBody(stream_id="s1")
    assert body.stream_id == "s1"
    assert body.shell == "/bin/sh"
    assert body.user == ""
    assert body.deployment_id == ""
    assert body.container_id == ""
    assert body.cols == 0
    assert body.rows == 0


def test_shell_open_round_trip_through_json():
    src = ShellOpenBody(
        stream_id="abc-123",
        shell="/bin/zsh",
        user="root",
        deployment_id="d1",
        container_id="ctr-deadbeef",
        cols=120,
        rows=40,
    )
    wire = json.loads(src.model_dump_json())
    rebuilt = ShellOpenBody.model_validate(wire)
    assert rebuilt == src


def test_shell_open_rejects_missing_stream_id():
    with pytest.raises(ValidationError):
        ShellOpenBody(shell="/bin/sh")  # type: ignore[call-arg]


def test_shell_input_empty_data_is_legal():
    """Worker may send a heartbeat-style empty input to keep the channel
    warm (or to test the wire); the protocol must not reject it."""
    body = ShellInputBody(stream_id="s1", data="")
    assert body.data == ""


def test_shell_input_large_payload_round_trips():
    """64 KiB chunks are the documented upper bound; verify Pydantic doesn't
    truncate or reject."""
    big = "x" * 65_536
    body = ShellInputBody(stream_id="s1", data=big)
    rebuilt = ShellInputBody.model_validate_json(body.model_dump_json())
    assert rebuilt.data == big
    assert len(rebuilt.data) == 65_536


def test_shell_input_carries_control_chars_unchanged():
    """Ctrl+C (^C, \\x03) and ANSI escapes must survive JSON round-trip
    so the dashboard can interrupt the running command."""
    payload = "ls\x03\x1b[1;31mERR\x1b[0m\n"
    body = ShellInputBody(stream_id="s1", data=payload)
    rebuilt = ShellInputBody.model_validate_json(body.model_dump_json())
    assert rebuilt.data == payload


def test_shell_input_carries_utf8_multibyte():
    """Non-ASCII data must round-trip (operators in non-Latin locales)."""
    payload = "echo 日本語 résumé 🚀\n"
    body = ShellInputBody(stream_id="s1", data=payload)
    rebuilt = ShellInputBody.model_validate_json(body.model_dump_json())
    assert rebuilt.data == payload


def test_shell_resize_round_trip():
    body = ShellResizeBody(stream_id="s1", cols=200, rows=50)
    wire = body.model_dump_json()
    rebuilt = ShellResizeBody.model_validate_json(wire)
    assert (rebuilt.cols, rebuilt.rows) == (200, 50)


def test_shell_resize_rejects_missing_dimensions():
    with pytest.raises(ValidationError):
        ShellResizeBody(stream_id="s1")  # type: ignore[call-arg]


def test_shell_close_minimal():
    body = ShellCloseBody(stream_id="s1")
    assert body.stream_id == "s1"


# --- ShellOutput / ShellExit / ShellError --------------------------------


def test_shell_output_default_data_blank():
    body = ShellOutputBody(stream_id="s1", data="")
    assert body.data == ""


def test_shell_output_preserves_nul_byte():
    """PTY output can include NULs from raw terminal traffic; the wire is
    JSON, which can encode NUL as ``\\u0000``."""
    payload = "before\x00after"
    body = ShellOutputBody(stream_id="s1", data=payload)
    rebuilt = ShellOutputBody.model_validate_json(body.model_dump_json())
    assert rebuilt.data == payload


def test_shell_exit_defaults():
    body = ShellExitBody(stream_id="s1")
    assert body.exit_code == 0
    assert body.reason == ""


def test_shell_exit_carries_nonzero_status():
    body = ShellExitBody(stream_id="s1", exit_code=137, reason="SIGKILL")
    rebuilt = ShellExitBody.model_validate_json(body.model_dump_json())
    assert rebuilt.exit_code == 137
    assert rebuilt.reason == "SIGKILL"


def test_shell_error_requires_message():
    with pytest.raises(ValidationError):
        ShellErrorBody(stream_id="s1")  # type: ignore[call-arg]


def test_shell_error_round_trip():
    body = ShellErrorBody(stream_id="s1", message="exec: /bin/zsh: no such file")
    rebuilt = ShellErrorBody.model_validate_json(body.model_dump_json())
    assert rebuilt.message == body.message


# --- LogsOpen / LogsLine / LogsEnd / LogsClose ----------------------------


def test_logs_open_default_targets():
    body = LogsOpenBody(stream_id="s1")
    assert body.deployment_id == ""
    assert body.container_id == ""


def test_logs_line_default_stream_is_stdout():
    body = LogsLineBody(stream_id="s1", data="hello")
    assert body.stream == "stdout"


def test_logs_line_rejects_unknown_stream_label():
    with pytest.raises(ValidationError):
        LogsLineBody(stream_id="s1", stream="garbage", data="x")  # type: ignore[arg-type]


def test_logs_line_round_trip_stderr():
    body = LogsLineBody(stream_id="s1", stream="stderr", data="boom\n")
    rebuilt = LogsLineBody.model_validate_json(body.model_dump_json())
    assert rebuilt.stream == "stderr"
    assert rebuilt.data == "boom\n"


def test_logs_end_default_reason():
    body = LogsEndBody(stream_id="s1")
    assert body.reason == ""


def test_logs_close_round_trip():
    body = LogsCloseBody(stream_id="s1")
    rebuilt = LogsCloseBody.model_validate_json(body.model_dump_json())
    assert rebuilt == body


# --- Envelope wrapping ----------------------------------------------------


def test_envelope_accepts_shell_open():
    env = Envelope(type="ShellOpen", id="e1", body={"stream_id": "s1"})
    assert env.type == "ShellOpen"


def test_envelope_rejects_unknown_message_type():
    """Dispatchers rely on the Literal — typos must blow up at parse
    time, not later when the body shape doesn't match."""
    with pytest.raises(ValidationError):
        Envelope(type="ShellOpened", id="e1", body={})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "env_type, body",
    [
        ("ShellOpen",   {"stream_id": "s"}),
        ("ShellInput",  {"stream_id": "s", "data": "ls\n"}),
        ("ShellResize", {"stream_id": "s", "cols": 80, "rows": 24}),
        ("ShellClose",  {"stream_id": "s"}),
        ("ShellOutput", {"stream_id": "s", "data": "hi"}),
        ("ShellExit",   {"stream_id": "s", "exit_code": 0}),
        ("ShellError",  {"stream_id": "s", "message": "boom"}),
        ("LogsOpen",    {"stream_id": "s"}),
        ("LogsLine",    {"stream_id": "s", "stream": "stdout", "data": "x"}),
        ("LogsEnd",     {"stream_id": "s"}),
        ("LogsClose",   {"stream_id": "s"}),
    ],
)
def test_envelope_wire_format_round_trip(env_type, body):
    env = Envelope(type=env_type, id=f"e-{env_type}", body=body)
    wire = json.loads(env.model_dump_json())
    rebuilt = Envelope.model_validate(wire)
    assert rebuilt.type == env_type
    assert rebuilt.body == body


# --- HeartbeatBody metrics field ------------------------------------------


from orchestration.workers.worker_controller.protocol import HeartbeatBody


def test_heartbeat_parses_optional_metrics():
    body = {
        "used": {"cpu_pct": "10.0"},
        "loaded_models": [],
        "metrics": {
            "ts": "2026-06-16T00:00:00Z",
            "cpu_pct": 10.0,
            "mem_used_bytes": 1024,
            "mem_total_bytes": 4096,
            "net_rx_bps": 5.0,
            "net_tx_bps": 6.0,
            "disk_read_bps": 7.0,
            "disk_write_bps": 8.0,
            "gpus": [
                {"index": 0, "name": "NVIDIA A100", "util_pct": 42.0,
                 "mem_used_mib": 100, "mem_total_mib": 81920},
            ],
        },
    }
    hb = HeartbeatBody.model_validate(body)
    assert hb.metrics is not None
    assert hb.metrics.cpu_pct == 10.0
    assert hb.metrics.gpus[0].util_pct == 42.0
    assert hb.metrics.gpus[0].mem_total_mib == 81920


def test_heartbeat_without_metrics_is_backcompat():
    hb = HeartbeatBody.model_validate({"used": {}, "loaded_models": []})
    assert hb.metrics is None

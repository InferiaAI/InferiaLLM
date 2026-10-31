"""``inferia-sim`` command line."""

import pytest

from simulator.cli import main


def test_list_scenarios(capsys):
    assert main(["list-scenarios"]) == 0
    assert "8xH100" in capsys.readouterr().out.split()


def test_validate_ok(capsys):
    assert main(["validate", "tiny"]) == 0
    assert capsys.readouterr().out.startswith("ok: tiny")


def test_validate_missing_file_is_a_clean_error(capsys):
    assert main(["validate", "nope.yaml"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("inferia-sim: error:") and "Traceback" not in err


def test_validate_invalid_scenario_is_a_clean_error(tmp_path, capsys):
    f = tmp_path / "bad.yaml"
    f.write_text("name: x\n")
    assert main(["validate", str(f)]) == 2
    assert "validation error" in capsys.readouterr().err


def test_version_and_help(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "inferia-sim" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([])


def test_run_writes_byte_identical_summaries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["run", "tiny", "--seed", "1", "--out", str(a), "--write-requests"]) == 0
    assert main(["run", "tiny", "--seed", "1", "--out", str(b)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("SIMULATED tiny seed=1")
    import json

    sa = json.loads((a / "summary.json").read_text())
    sb = json.loads((b / "summary.json").read_text())
    # Only the artifact list differs (a wrote requests.csv); everything else is identical.
    assert sa["artifacts"]["requests"] == "requests.csv" and sb["artifacts"]["requests"] is None
    sa["artifacts"] = sb["artifacts"]
    assert sa == sb
    assert (a / "manifest.json").read_bytes() == (b / "manifest.json").read_bytes()
    assert sa["manifest"]["created_at"] == "2026-01-01T00:00:00Z"
    assert (a / "requests.csv").read_text().splitlines()[0].startswith("rid,workload_class")


def test_run_twice_same_args_is_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    for d in ("x", "y"):
        assert main(["run", "tiny", "--out", str(tmp_path / d)]) == 0
    assert (tmp_path / "x" / "summary.json").read_bytes() == (tmp_path / "y" / "summary.json").read_bytes()


def test_run_with_invariant_checks_on_a_small_cache(tmp_path, capsys):
    import yaml

    from simulator.config import read_scenario_bytes

    d = yaml.safe_load(read_scenario_bytes("tiny"))
    d["deployment"][0].update(gpu_memory_utilization=0.24, max_model_len=2048)
    d["workload"]["classes"][0]["input_tokens"]["max"] = 1024
    d["workload"]["classes"][0]["output_tokens"]["max"] = 512
    d["run"].update(duration_s=20, warmup_s=5)
    f = tmp_path / "small.yaml"
    f.write_text(yaml.safe_dump(d))
    assert main(["run", str(f), "--out", str(tmp_path / "o"), "--check-invariants"]) == 0
    assert "SIMULATED tiny" in capsys.readouterr().out

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

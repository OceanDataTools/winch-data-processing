import csv
from datetime import timedelta

from conftest import CONFIG, START, cast, write_cruise

from winch_data_processing.cli import build_parser, main


def _cruise(tmp_path):
    first = cast(START, max_payout=500)
    second = cast(first[-1][0] + timedelta(minutes=30), max_payout=800)
    data = write_cruise(tmp_path / "data", first, second)
    config = tmp_path / "TEST01_CTD.toml"
    config.write_text(CONFIG)
    return data, config


def test_run_writes_the_wire_log_and_checks(tmp_path, capsys):
    data, config = _cruise(tmp_path)
    output = tmp_path / "output"
    assert main(["run", str(data), "-c", str(config), "-o", str(output)]) == 0
    printed = capsys.readouterr().out
    assert "TEST01_CTD: 6" in printed and "2 deployments" in printed
    text = (output / "TEST01_CTD_wire_log.csv").read_text()
    header = [line for line in text.splitlines() if line.startswith("#")]
    assert "# tension_member: NSF-TEST" in header
    assert "# rvss_table: RVSS Appendix A Table A.8.2" in header
    assert "# sheave: Block: D 16.000 in, w 0.400 in, D/d 49.7" in header
    rows = list(csv.DictReader(line for line in text.splitlines() if not line.startswith("#")))
    assert [r["cast"] for r in rows] == ["1", "2"]
    assert [r["max_payout"] for r in rows] == ["500.0", "800.0"]
    assert float(rows[1]["payout_at_max_tension"]) > 790  # the bottom, give or take the swell
    assert rows[0]["start"].endswith("Z") and rows[0]["logging_rate"] == "20.0"
    checks = list(csv.DictReader((output / "TEST01_CTD_checks.csv").open()))
    assert {c["status"] for c in checks} <= {"pass", "info", "n/a"}


def test_run_refuses_to_overwrite_and_exits_1_on_a_failed_check(tmp_path, capsys):
    data, config = _cruise(tmp_path)
    output = tmp_path / "output"
    assert main(["run", str(data), "-c", str(config), "-o", str(output)]) == 0
    assert main(["run", str(data), "-c", str(config), "-o", str(output)]) == 1
    assert "exists; --force to overwrite" in capsys.readouterr().err
    config.write_text(CONFIG.replace("display_rate = 10", "display_rate = 1"))
    assert main(["run", str(data), "-c", str(config), "-o", str(output), "--force"]) == 1
    assert "FAIL  Tension display refresh rate" in capsys.readouterr().out


def test_expected_errors_are_one_line(tmp_path, capsys):
    config = tmp_path / "bad.toml"
    config.write_text(CONFIG.replace("[winch]", "[winch]\ncolour = 1"))
    assert main(["run", str(tmp_path), "-c", str(config)]) == 1
    err = capsys.readouterr().err.strip()
    assert len(err.splitlines()) == 1 and "unknown key 'winch.colour'" in err


def test_formats_lists_the_readers(capsys):
    assert main(["formats"]) == 0
    assert capsys.readouterr().out.split()[0] == "lci90i"


def test_verbose_is_accepted_before_or_after_the_command():
    parser = build_parser()
    assert parser.parse_args(["-v", "formats"]).verbose == 1
    assert parser.parse_args(["formats", "-vv"]).verbose == 2

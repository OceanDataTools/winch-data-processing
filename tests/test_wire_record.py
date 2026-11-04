"""The wire record (wirerecord.py, record_report.py): a tension member's deployments across
cruises, merged from each run, and its report.
"""

from datetime import datetime, timedelta

import pytest
from conftest import CONFIG, START, cast, write_cruise
from pypdf import PdfReader

from winch_data_processing.assess import assess
from winch_data_processing.cli import main
from winch_data_processing.config import loads_config
from winch_data_processing.wirerecord import (
    DEPLOYMENTS,
    WireRecordError,
    load,
    merge,
    record_dir,
    totals,
)

LATER = datetime(2026, 8, 1, 12)


def cruise_config(cruise: str, break_test: str = "2026-01-10") -> str:
    return CONFIG.replace('id = "TEST01"', f'id = "{cruise}"\nvessel_name = "Sally Ride"').replace(
        "date = 2026-01-10", f"date = {break_test}"
    )


def run_cruise(tmp_path, cruise: str, start: datetime, casts: int, break_test="2026-01-10"):
    rows, at = [], start
    for _ in range(casts):
        rows += cast(at, rate=2, max_payout=400)
        at = rows[-1][0] + timedelta(minutes=30)
    data = write_cruise(tmp_path / cruise / str(casts), rows)
    return assess(data, loads_config(cruise_config(cruise, break_test)))


def test_cruises_merge_into_one_record_and_a_rerun_replaces_its_own(tmp_path):
    records = tmp_path / "records"
    merge(records, run_cruise(tmp_path, "SR2611", START, 2))
    merge(records, run_cruise(tmp_path, "SR2614", LATER, 3))
    record = load(record_dir(records, "NSF-TEST"))
    assert [(d.cruise, d.number) for d in record.deployments] == [
        ("SR2611", 1), ("SR2611", 2), ("SR2614", 1), ("SR2614", 2), ("SR2614", 3),
    ]  # fmt: skip
    assert {d.vessel for d in record.deployments} == {"Sally Ride"}
    # Run again with one deployment found: only that cruise's rows are replaced
    merge(records, run_cruise(tmp_path, "SR2611", START, 1))
    record = load(record_dir(records, "NSF-TEST"))
    assert [(d.cruise, d.number) for d in record.deployments] == [
        ("SR2611", 1), ("SR2614", 1), ("SR2614", 2), ("SR2614", 3),
    ]  # fmt: skip
    found = totals(record.deployments)
    assert (found.deployments, found.cruises, found.deepest) == (4, 2, 400.0)


def test_since_the_last_break_test(tmp_path):
    records = tmp_path / "records"
    merge(records, run_cruise(tmp_path, "SR2611", START, 2))
    # The later cruise's config has a break test between the two cruises
    merge(records, run_cruise(tmp_path, "SR2614", LATER, 3, break_test="2026-07-15"))
    record = load(record_dir(records, "NSF-TEST"))
    assert str(record.last_break_test) == "2026-07-15"
    assert [d.cruise for d in record.since_last_break_test()] == ["SR2614"] * 3
    # A deployment on the test's day counts: a test has a date, not a time
    merge(records, run_cruise(tmp_path, "SR2614", LATER, 3, break_test="2026-08-01"))
    assert len(load(record_dir(records, "NSF-TEST")).since_last_break_test()) == 3


def test_a_record_without_a_break_test_counts_every_deployment(tmp_path):
    result = run_cruise(tmp_path, "SR2611", START, 2)
    text = (
        CONFIG.split("[[tension_member.break_test]]")[0]
        + "[[sheave]]"
        + CONFIG.split("[[sheave]]")[1]
    )
    result = assess(result.directory, loads_config(text))
    directory = merge(tmp_path / "records", result)
    record = load(directory)
    assert record.last_break_test is None and len(record.since_last_break_test()) == 2


def test_what_isnt_a_wire_record(tmp_path, capsys):
    with pytest.raises(WireRecordError, match="has no deployments.csv"):
        load(tmp_path)
    (tmp_path / DEPLOYMENTS).write_text("time,tension\n")
    with pytest.raises(WireRecordError, match="not a wire record's deployments"):
        load(tmp_path)
    assert main(["wire-record", "report", str(tmp_path)]) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_run_merges_and_the_report_says_what_a521_asks(tmp_path, capsys):
    data = write_cruise(tmp_path / "data", cast(START, max_payout=500))
    config = tmp_path / "SR2611_CTD.toml"
    config.write_text(cruise_config("SR2611", "2026-06-01"))
    records = tmp_path / "records"
    command = ["run", str(data), "-c", str(config), "-o", str(tmp_path / "out")]
    assert main([*command, "--wire-record", str(records)]) == 0
    assert "merged 1 deployment into" in capsys.readouterr().out
    directory = record_dir(records, "NSF-TEST")
    assert main(["wire-record", "report", str(directory)]) == 0
    out = capsys.readouterr().out
    assert "Since the last break test (2026-06-01): 1 deployment," in out
    pdf = directory / "NSF-TEST_wire_record.pdf"
    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "Wire record: NSF-TEST" in text and "Since the last break test (2026-06-01)" in text
    assert "SR2611" in text and "Sally Ride" in text and "Break test" in text
    # Not replaced without --force, and identical when it is
    first = pdf.read_bytes()
    assert main(["wire-record", "report", str(directory)]) == 1
    assert main(["wire-record", "report", str(directory), "--force"]) == 0
    assert pdf.read_bytes() == first

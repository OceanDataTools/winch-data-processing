"""The wire record (wirerecord.py, record_report.py): a tension member's deployments across
cruises, merged from each run, and its report.
"""

import tomllib
from datetime import datetime, timedelta

import pytest
from conftest import CONFIG, ROOT, START, cast, write_cruise
from pypdf import PdfReader

from winch_data_processing.assess import assess
from winch_data_processing.cli import main
from winch_data_processing.config import loads_config
from winch_data_processing.wirerecord import (
    DEPLOYMENTS,
    EVENTS,
    EVENTS_SCHEMA,
    MEMBER,
    WireRecordError,
    load,
    load_events,
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


# The operator's events (events.toml) and the record's break tests -----------------------------

EXAMPLE_EVENTS = ROOT / "configs" / "events.example.toml"


def events(directory, text: str):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / EVENTS).write_text(text)
    return directory


def test_the_example_events_show_every_key():
    data = tomllib.loads(EXAMPLE_EVENTS.read_text())
    for kind, keys in EVENTS_SCHEMA.items():
        if keys is None:
            assert kind in data, kind
        else:
            assert set(data[kind][0]) == set(keys), kind
    found = load_events(EXAMPLE_EVENTS)
    assert [e.kind for e in found] == [
        "spooling", "break_test", "lubrication", "cutback", "termination", "test",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[[cutback]]\ndate = 2026-02-03\nlength = 50\nwhy = 'x'\n", "unknown key 'cutback.why'"),
        ("[[cutback]]\ndate = 2026-02-03\n", r"\[\[cutback\]\] 1 has no length"),
        ("[[cutback]]\ndate = '2026-02-03'\nlength = 50\n", "date must be a date"),
        ("[[cutback]]\ndate = 2026-02-03\nlength = -5\n", "length must be a positive number"),
        ("[[test]]\ndate = 2026-02-03\nkind = 'TDR'\nresult = 12\n", "result must be text"),
        ("[cutback]\ndate = 2026-02-03\nlength = 5\n", r"an array of tables \(\[\[cutback\]\]\)"),
        ("length_unit = 'fathom'\n", 'length unit "fathom" is not one of'),
    ],
)
def test_events_that_cant_be_read(tmp_path, text, message):
    with pytest.raises(WireRecordError, match=message):
        load_events(events(tmp_path, text) / EVENTS)


def test_events_in_other_units_and_the_length_after_cutbacks(tmp_path):
    text = (
        "tension_unit = 'kN'\nlength_unit = 'ft'\n"
        "[[cutback]]\ndate = 2025-01-01\nlength = 100\n"  # before the spooling: not counted
        "[[spooling]]\ndate = 2025-06-12\nlength = 10000\n"
        "[[cutback]]\ndate = 2026-02-03\nlength = 50\n"
        "[[break_test]]\ndate = 2025-08-01\nload = 45\n"
    )
    record = load(events(tmp_path, text))
    length, cut = record.length()
    assert (round(length, 2), round(cut, 2)) == (3032.76, 15.24)  # 9,950 ft, 50 ft
    assert record.break_tests[0].load == pytest.approx(45 * 224.808943)
    assert record.deployments == []  # its events only, before its first cruise
    no_spooling = load(events(tmp_path / "b", "[[cutback]]\ndate = 2026-02-03\nlength = 50\n"))
    assert no_spooling.length() == (None, 50.0)


def test_break_tests_from_every_config_and_the_events(tmp_path):
    records = tmp_path / "records"
    merge(records, run_cruise(tmp_path, "SR2611", START, 1, break_test="2026-01-10"))
    merge(records, run_cruise(tmp_path, "SR2614", LATER, 1, break_test="2026-07-15"))
    directory = record_dir(records, "NSF-TEST")
    record = load(directory)  # both configs' tests kept, not only the latest's
    assert [str(t.date) for t in record.break_tests] == ["2026-01-10", "2026-07-15"]
    events(directory, "[[break_test]]\ndate = 2026-09-01\nload = 9800\n")
    merge(records, run_cruise(tmp_path, "SR2614", LATER, 1, break_test="2026-07-15"))
    member = (directory / MEMBER).read_text()
    assert "2026-09-01" not in member  # events.toml's tests stay there, not copied
    assert str(load(directory).last_break_test) == "2026-09-01"
    # A date with two loads is the operator's to correct
    events(directory, "[[break_test]]\ndate = 2026-07-15\nload = 9000\n")
    with pytest.raises(
        WireRecordError,
        match="2026-07-15 is 10000 lbf in tension_member.toml and 9000 lbf in events.toml",
    ):
        _ = load(directory).break_tests


def test_a_break_test_entered_once_sets_every_runs_abl(tmp_path, capsys):
    data = write_cruise(tmp_path / "data", cast(START, max_payout=500))
    config = tmp_path / "SR2611_CTD.toml"
    without_tests = CONFIG.split("[[tension_member.break_test]]")[0] + "[[sheave]]"
    config.write_text(without_tests + CONFIG.split("[[sheave]]")[1])
    records = tmp_path / "records"
    events(record_dir(records, "NSF-TEST"), "[[break_test]]\ndate = 2026-05-01\nload = 9000\n")
    command = ["run", str(data), "-c", str(config), "-o", str(tmp_path / "out")]
    main([*command, "--wire-record", str(records)])
    checks = (tmp_path / "out" / "TEST01_CTD_checks.csv").read_text()
    assert "ABL 9000 lbf, 90 % of the NBL" in checks
    assert "no [[tension_member.break_test]]" not in checks
    # Without the record, the config alone has no break test
    main([*command, "--force"])
    assert (
        "no [[tension_member.break_test]]"
        in (tmp_path / "out" / "TEST01_CTD_checks.csv").read_text()
    )


def test_the_report_has_the_maintenance_and_events(tmp_path, capsys):
    records = tmp_path / "records"
    directory = merge(records, run_cruise(tmp_path, "SR2611", START, 1))
    (directory / EVENTS).write_text(EXAMPLE_EVENTS.read_text())
    assert main(["wire-record", "report", str(directory)]) == 0
    out = capsys.readouterr().out
    assert "Length: 7,950.0 m: 8,000.0 m spooled on 2025-06-12, 50.0 m cut back since" in out
    assert "Last lubrication: 2025-12-15: Nyrosten T-50, 3,000.0 m; the outer 3,000 m" in out
    assert "Last test: resistance: 2026-02-04" in out
    pdf = directory / "NSF-TEST_wire_record.pdf"
    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "Maintenance" in text and "Events" in text and "re-terminating" in text
    assert "2025-08-01: 10400 lbf" in text  # events.toml's break test with the config's

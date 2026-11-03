from datetime import date

import pytest
from conftest import CONFIG, START, cast, write_cruise

from winch_data_processing.assess import assess
from winch_data_processing.checks import Status, add_months
from winch_data_processing.config import loads_config


def _checks(tmp_path, rows, config_text=CONFIG) -> dict:
    result = assess(write_cruise(tmp_path, rows), loads_config(config_text))
    return {c.name: c for c in result.checks}


def test_a_cast_within_its_table_passes_every_data_check(tmp_path):
    # FS 2.5 (Table A.8.2): SWT 4,000 lbf; 1,000 m of 0.3 lbf/m under 800 lbf, swell 100
    checks = _checks(tmp_path, cast(START, rate=20, max_payout=1000))
    for name in (
        "Tension within the safe working tension",
        "Never below the lowest FS allowed",
        "Peak tension within the elastic limit",
        "Tension logging rate",
        "Gaps in the tension record",
        "Tension alarm setting",
        "Tension display refresh rate",
        "Tension measuring system calibration",
        "Tension measuring accuracy",
        "Sheaves and load-carrying rollers",
        "Break test interval",
        "Assigned breaking load",
    ):
        assert checks[name].status is Status.PASS, (name, checks[name].detail)
    assert checks["Deployments"].detail == "1 found"
    assert checks["Alarm conditions in the logged data"].status is Status.INFO


def test_tension_beyond_the_swt_elastic_limit_and_lowest_fs_fails(tmp_path):
    # 10,000 lbf ABL: SWT 4,000 (FS 2.5), lowest 5,000 (FS 2.0), elastic limit 5,556 (FS 1.8)
    checks = _checks(tmp_path, cast(START, max_payout=1000, base=4600, per_metre=1, swell=50))
    swt = checks["Tension within the safe working tension"]
    assert swt.status is Status.FAIL and "cast 1:" in swt.detail and "FS 1.77" in swt.detail
    assert checks["Never below the lowest FS allowed"].status is Status.FAIL
    elastic = checks["Peak tension within the elastic limit"]
    assert elastic.status is Status.FAIL and "cut back or retired" in elastic.detail


def test_the_logging_rate_the_table_requires(tmp_path):
    rows = cast(START, rate=4, max_payout=300)
    assert _checks(tmp_path, rows)["Tension logging rate"].status is Status.PASS  # 3 Hz
    text = CONFIG.replace("factor_of_safety = 2.5", "factor_of_safety = 2.2")  # A.8.3: 20 Hz
    rate = _checks(tmp_path, rows, text)["Tension logging rate"]
    assert rate.status is Status.FAIL and rate.detail == "below 20 Hz: cast 1: 4.0 Hz"
    text = CONFIG.replace("factor_of_safety = 2.5", "factor_of_safety = 5")  # A.8.1: none
    assert _checks(tmp_path, rows, text)["Tension logging rate"].status is Status.NA


@pytest.mark.parametrize(
    ("old", "new", "check", "status"),
    [
        ("alarm_tension = 3500", "alarm_tension = 3600", "Tension alarm setting", Status.FAIL),
        ("alarm_tension = 3500\n", "", "Tension alarm setting", Status.WARN),
        ("display_rate = 10", "display_rate = 2", "Tension display refresh rate", Status.FAIL),
        ("accuracy = 2.0", "accuracy = 5", "Tension measuring accuracy", Status.FAIL),
        (
            "last_calibration = 2026-05-01",
            "last_calibration = 2025-12-18",
            "Tension measuring system calibration",
            Status.FAIL,
        ),
        (
            "tread_diameter = 16.0",
            "tread_diameter = 14.0",
            "Sheaves and load-carrying rollers",
            Status.FAIL,
        ),
        (
            "groove_width = 0.40",
            "groove_width = 0.50",
            "Sheaves and load-carrying rollers",
            Status.FAIL,
        ),
        ("date = 2026-01-10", "date = 2024-06-01", "Break test interval", Status.FAIL),
        ("\nload = 10000", "\nload = 4900", "Assigned breaking load", Status.FAIL),
    ],
)
def test_config_checks(tmp_path, old, new, check, status):
    assert old in CONFIG
    found = _checks(tmp_path, cast(START, max_payout=200), CONFIG.replace(old, new))[check]
    assert found.status is status, found.detail


def test_a_ten_percent_decrease_shortens_the_break_test_interval(tmp_path):
    text = CONFIG.replace(
        "date = 2026-01-10\nload = 10000",
        "date = 2025-08-10\nload = 8800\n\n[[tension_member.break_test]]\ndate = 2024-06-10\nload = 10000",
    )
    found = _checks(tmp_path, cast(START, max_payout=200), text)["Break test interval"]
    assert found.status is Status.PASS and "every 12 months, after a 10 % decrease" in found.detail
    text = text.replace("factor_of_safety = 2.5", "factor_of_safety = 2.2")  # 12 -> 6 months
    found = _checks(tmp_path, cast(START, max_payout=200), text)["Break test interval"]
    assert found.status is Status.FAIL and "due 2026-02-10" in found.detail


def test_add_months_clips_to_the_months_end():
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 15), 24) == date(2028, 1, 15)

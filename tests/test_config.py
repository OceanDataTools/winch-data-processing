from datetime import date

import pytest
from conftest import CONFIG, EXAMPLE

from winch_data_processing.config import ConfigError, load_config, loads_config
from winch_data_processing.rvss import Kind, StandardError


def test_the_example_loads():
    config = load_config(EXAMPLE)
    member = config.tension_member
    assert member.kind is Kind.CABLE and member.table.name == "Table A.8.2"
    assert member.assigned_breaking_load == 10000  # the TBL above the NBL isn't used
    assert member.safe_working_tension == 4000
    assert len(config.sheaves) == 2 and config.data.format == "openrvdas_lci90i"


def _with(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new)


def test_the_abl_is_the_latest_tbl_below_the_nbl():
    text = CONFIG.replace(
        "date = 2026-01-10\nload = 10000",
        "date = 2026-01-10\nload = 8900\n\n[[tension_member.break_test]]\ndate = 2024-01-10\nload = 9800",
    )
    member = loads_config(text).tension_member
    assert [t.date for t in member.break_tests] == [date(2024, 1, 10), date(2026, 1, 10)]
    assert member.assigned_breaking_load == 8900
    assert not member.abl_decreased  # 9 % below the 9,800 before it
    member = loads_config(text.replace("load = 8900", "load = 8800")).tension_member
    assert member.abl_decreased


def test_a_cutback_and_retest_can_raise_the_abl():
    text = CONFIG.replace(
        "date = 2026-01-10\nload = 10000",
        "date = 2026-01-10\nload = 9900\n\n[[tension_member.break_test]]\ndate = 2025-01-10\nload = 8000",
    )
    member = loads_config(text).tension_member
    assert member.assigned_breaking_load == 9900 and not member.abl_decreased


def test_units_are_converted():
    text = _with(
        CONFIG, 'kind = "cable"', 'kind = "cable"\ntension_unit = "kN"\ndiameter_unit = "mm"'
    )
    text = _with(text, "nominal_breaking_load = 10000", "nominal_breaking_load = 44.48")
    text = _with(text, "diameter = 0.322", "diameter = 8.18")
    member = loads_config(text.replace("load = 10000", "load = 44.48")).tension_member
    assert member.nominal_breaking_load == pytest.approx(9999.5, abs=1)
    assert member.diameter == pytest.approx(0.322, abs=1e-3)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("[winch]", "[winch]\ncolour = 1", "unknown key 'winch.colour'; \\[winch\\] takes"),
        ("[cruise]", "[crusie]", "unknown key 'crusie'"),
        ('kind = "cable"', 'kind = "chain"', 'kind "chain" is not one of'),
        ('format = "openrvdas_lci90i"', 'format = "motu"', 'format "motu" is not one of'),
        ('id = "NSF-TEST"\n', "", "\\[tension_member\\] needs id"),
        ("[[sheave]]", "[sheave]", "sheave is an array of tables"),
        ("last_calibration = 2026-05-01", 'last_calibration = "May"', "is not a date"),
        ('format = "openrvdas_lci90i"', 'format = "delimited"', "needs \\[data.columns\\]"),
        ('files = "*.txt"', 'files = "*.txt"\ntension_unit = "lb"', 'tension unit "lb"'),
    ],
)
def test_bad_configs_say_what_is_wrong(old, new, message):
    with pytest.raises(ConfigError, match=message):
        loads_config(_with(CONFIG, old, new))


def test_an_fs_the_standard_forbids_is_refused():
    with pytest.raises(StandardError, match="cable may not be operated below FS 2"):
        loads_config(_with(CONFIG, "factor_of_safety = 2.5", "factor_of_safety = 1.8"))

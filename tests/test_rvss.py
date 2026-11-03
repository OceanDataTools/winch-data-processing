import pytest

from winch_data_processing import rvss
from winch_data_processing.rvss import Kind, StandardError, table_for


@pytest.mark.parametrize(
    ("kind", "fs", "table"),
    [
        (Kind.CABLE, 5.0, "Table A.8.1"),
        (Kind.CABLE, 8.0, "Table A.8.1"),
        (Kind.CABLE, 4.99, "Table A.8.2"),
        (Kind.CABLE, 2.5, "Table A.8.2"),
        (Kind.CABLE, 2.0, "Table A.8.3"),
        (Kind.WIRE_ROPE, 2.4, "Table A.8.3"),
        (Kind.WIRE_ROPE, 1.99, "Table A.8.4"),
        (Kind.WIRE_ROPE, 1.5, "Table A.8.4"),
        (Kind.SYNTHETIC, 5.0, "Table A.8.5"),
    ],
)
def test_the_selected_fs_picks_its_table(kind, fs, table):
    assert table_for(kind, fs).name == table


@pytest.mark.parametrize(
    ("kind", "fs"), [(Kind.CABLE, 1.9), (Kind.WIRE_ROPE, 1.4), (Kind.SYNTHETIC, 4.0)]
)
def test_below_the_kinds_lowest_fs_is_refused(kind, fs):
    with pytest.raises(StandardError, match="may not be operated below"):
        table_for(kind, fs)


def test_the_tables_cover_every_allowed_fs_once():
    for kind in Kind:
        for fs in (rvss.MIN_FS[kind] + step / 100 for step in range(600)):
            holding = [t for t in rvss.TABLES if kind in t.kinds and t.holds(fs)]
            assert len(holding) == 1, (kind, fs)


def test_requirements_tighten_with_the_tables():
    assert (rvss.A_8_2.logging_hz, rvss.A_8_3.logging_hz, rvss.A_8_4.logging_hz) == (3, 20, 20)
    assert (rvss.A_8_2.alarm_fs, rvss.A_8_3.alarm_fs, rvss.A_8_4.alarm_fs) == (2.8, 2.2, 1.7)
    assert rvss.A_8_1.logging_hz is None and rvss.A_8_1.alarm_fs is None
    assert rvss.A_8_3.break_test_months == 12 and rvss.A_8_3.break_test_months_after_decrease == 6

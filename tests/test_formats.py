from dataclasses import replace

import numpy as np
import pytest
from conftest import CONFIG, START, cast, lci_lines, rd, write_cruise

from winch_data_processing.config import Columns, loads_config
from winch_data_processing.formats import delimited, lci90i
from winch_data_processing.rawdata import read_fileset

SOURCE = loads_config(CONFIG).data


def test_logged_lci90i_takes_the_loggers_clock_by_default():
    lines = [  # RR2403's hydro winch, 2024-03-29: the display's clock 2 h 21 min ahead
        "2024-03-29T18:32:58.149166Z 02RD,2024-03-29T20:53:35.302,-0000046,00000000,000664.5,2843",
        "2024-03-29T18:32:58.199563Z 02RD,2024-03-29T20:53:35.352,-0000044,00000000,000664.5,2846",
    ]
    found = lci90i.read_logged(lines, SOURCE, None)
    assert found.time.astype(str).tolist() == [
        "2024-03-29T18:32:58.149166000",
        "2024-03-29T18:32:58.199563000",
    ]
    assert found.tension.tolist() == [-46, -44] and found.payout.tolist() == [664.5, 664.5]
    assert found.speed.tolist() == [0, 0] and found.names == {"02": 2}
    instrument = lci90i.read_logged(lines, replace(SOURCE, time="instrument"), None)
    assert str(instrument.time[0]) == "2024-03-29T20:53:35.302000000"


def test_lines_that_cant_be_read_are_counted_by_reason():
    t = "2024-04-06T22:10:00.000"
    good = rd(t, 668, 60, 333.6)
    split_at = good.index(",", good.index(",", good.index(",", 5) + 1) + 1)  # after the speed
    bad_number = "02RD,2024-04-06T22:10:00.000,-000003i,00000000,000664.5,"
    lines = [
        "not a record",
        f"2024-04-06T22:10:00.0Z {good[:split_at]}",  # split across two logger lines
        f"2024-04-06T22:10:00.1Z {good[split_at:]}",
        f"2024-04-06T22:10:00.2Z {good[:-1]}9",  # a wrong digit
        "2024-04-06T22:10:00.3Z 03SU,L,0140,04,X2,1006",
        f"2024-04-06T22:10:00.4Z {rd(t, 668, 60, 333.6, lan='03')}",
        f"2024-04-06T22:10:00.5Z {bad_number}{sum(bad_number.encode())}",
        f"2024-04-06T22:10:00.6Z {good[:split_at]}",  # split, its end lost
        f"2024-04-06T22:10:00.7Z {good}",
        "",
    ]
    found = lci90i.read_logged(lines, SOURCE, "02")
    assert len(found) == 2 and str(found.time[0]) == "2024-04-06T22:10:00.000000000"
    assert found.skipped == {
        "no logger timestamp": 1,
        "bad checksum": 1,
        "SU message": 1,
        "another winch": 1,
        "bad number": 1,
        "split record without its end": 1,
    }
    assert found.names == {"02": 3, "03": 1}  # a bad checksum, before its name is read


def test_checksum_is_the_byte_sum_before_it():
    assert lci90i.checksum_matches("02RD,2024-03-29T20:53:35.302,-0000046,00000000,000664.5,2843")
    assert not lci90i.checksum_matches(
        "0bRD,2024-03-29T20:53:35.302,-0000046,00000000,000664.5,2843"
    )


def test_openrvdas_definitions_named_record_is_read():
    line = "2026-06-19T12:00:00Z 01RD,2026-06-19T12:00:00,CTD,812,-12.5,1.9,1A2B"
    found = lci90i.read_logged([line], SOURCE, None)
    assert found.tension.tolist() == [812] and found.names == {"CTD": 1}


def test_the_lci90i_own_log_is_timed_by_its_clock():
    found = lci90i.read(lci_lines(cast(START)[:3], logged=False), SOURCE, None)
    assert len(found) == 3 and str(found.time[0]) == "2026-06-19T12:00:00.000000000"
    bad = "02RD,2026-13-01T00:00:00,00000001,00000002,000003.0,"
    assert lci90i.read([f"{bad}{sum(bad.encode())}"], SOURCE, None).skipped == {"bad time": 1}


@pytest.mark.parametrize(
    ("time_format", "text"),
    [
        ("iso", "2026-06-19T12:00:00.5Z"),
        ("epoch", "1781870400.5"),
        ("%m/%d/%Y %H:%M:%S.%f", "06/19/2026 12:00:00.5"),
        ("%Y-%m-%d %H:%M:%S.%f%z", "2026-06-19 14:00:00.5+0200"),
    ],
)
def test_delimited_times(time_format, text):
    source = replace(
        SOURCE,
        format="delimited",
        columns=Columns(time="Time", tension="Tension [lbf]", payout=3),
        time_format=time_format,
        header_lines=2,
    )
    lines = ["exported by the winch", "Time,Tension [lbf],Speed,Payout", f'"{text}",1200,30,450.5']
    found = delimited.read(lines, source, None)
    assert str(found.time[0]) == "2026-06-19T12:00:00.500000000"
    assert found.tension.tolist() == [1200] and found.payout.tolist() == [450.5]
    assert np.isnan(found.speed[0])


def test_read_fileset_converts_units_and_keeps_the_cruise(tmp_path):
    rows = cast(START, rate=1, max_payout=100)
    write_cruise(tmp_path, rows)
    text = CONFIG.replace(
        'files = "*.txt"', 'files = "*.txt"\ntension_unit = "kN"\npayout_unit = "ft"'
    )
    data = read_fileset(tmp_path, loads_config(text))
    assert len(data) == len(rows) and data.files == [tmp_path / "winch.txt"]
    assert data.tension[0] == pytest.approx(800 * 224.808943, rel=1e-6)
    assert np.nanmax(data.payout) == pytest.approx(100 * 0.3048)
    outside = loads_config(CONFIG.replace('id = "TEST01"', 'id = "TEST01"\nstart = 2026-06-20'))
    assert len(read_fileset(tmp_path, outside)) == 0


def test_files_are_sorted_into_one_series(tmp_path):
    rows = cast(START, rate=1, max_payout=50)
    (tmp_path / "b.txt").write_text("\n".join(lci_lines(rows[: len(rows) // 2])))
    (tmp_path / "a.txt").write_text("\n".join(lci_lines(rows[len(rows) // 2 :])))
    data = read_fileset(tmp_path, loads_config(CONFIG))
    assert len(data) == len(rows) and (np.diff(data.time) > np.timedelta64(0)).all()
    assert [p.name for p in data.files] == ["a.txt", "b.txt"]

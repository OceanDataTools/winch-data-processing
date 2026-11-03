from datetime import timedelta

import numpy as np
import pytest
from conftest import CONFIG, START, cast, write_cruise

from winch_data_processing.casts import find_casts
from winch_data_processing.config import Thresholds, loads_config
from winch_data_processing.rawdata import read_fileset


def _data(tmp_path, *casts_rows):
    return read_fileset(write_cruise(tmp_path, *casts_rows), loads_config(CONFIG))


def test_a_cast_and_its_figures(tmp_path):
    data = _data(tmp_path, cast(START, max_payout=1000, swell=0))
    (found,) = find_casts(data, Thresholds())
    assert found.number == 1 and found.samples == len(data)  # padding takes the deck time too
    assert found.max_payout == pytest.approx(1000, abs=0.1)
    assert found.max_tension == pytest.approx(800 + 0.3 * 1000, abs=1)
    # tension in whole lbf: the first of the peak's equal values, just before the bottom
    assert found.payout_at_max_tension == pytest.approx(1000, abs=3)
    assert found.logging_rate == pytest.approx(20) and found.longest_gap == pytest.approx(0.05)
    assert found.duration == pytest.approx((len(data) - 1) / 20)


def test_casts_are_split_and_padded_to_the_midpoint(tmp_path):
    first = cast(START, max_payout=200, soak=30)
    second = cast(first[-1][0] + timedelta(seconds=200), max_payout=300, soak=30)
    data = _data(tmp_path, first, second)
    one, two = find_casts(data, Thresholds())
    assert (one.number, two.number) == (1, 2)
    assert one.stop == two.start  # the gap's midpoint divides them
    assert one.max_payout == pytest.approx(200, abs=0.1)
    assert two.max_payout == pytest.approx(300, abs=0.1)


def _profile(*points, rate=20.0, tension=800.0):
    """(time, tension, payout, speed) rows along the (seconds, payout) breakpoints"""
    t = np.arange(0, points[-1][0], 1 / rate)
    payout = np.interp(t, *zip(*points, strict=True))
    return [
        (START + timedelta(seconds=float(s)), tension, float(p), 0.0)
        for s, p in zip(t, payout, strict=True)
    ]


def test_a_short_dip_doesnt_split_a_cast(tmp_path):
    # down to 20 m, up to 5 m to soak for 60 s, then down to 500 m and back
    rows = _profile(
        (0, 0), (60, 0), (80, 20), (95, 5), (155, 5), (170, 20), (650, 500), (1150, 0), (1200, 0)
    )
    assert len(find_casts(_data(tmp_path, rows), Thresholds())) == 1
    assert len(find_casts(_data(tmp_path, rows), Thresholds(merge_gap=30))) == 1  # 20 m: < 60 s
    assert (
        len(find_casts(_data(tmp_path, rows), Thresholds(merge_gap=30, min_cast_duration=5))) == 2
    )


def test_a_stale_counter_and_its_reset_are_not_a_cast(tmp_path):
    # RR2403's hydro winch: 664.5 m, idle, until zeroed before its cast
    rows = _profile(
        (0, 664.5), (600, 664.5), (600.05, 0), (660, 0), (1000, 300), (1340, 0), (1400, 0)
    )
    (found,) = find_casts(_data(tmp_path, rows), Thresholds())
    assert found.max_payout == pytest.approx(300, abs=0.1)
    assert found.start_time < np.datetime64(START + timedelta(seconds=600))  # padded past it


def test_short_excursions_are_not_casts(tmp_path):
    rows = cast(START, max_payout=20, speed=60, soak=10)  # 20 m and back in 40 s
    assert find_casts(_data(tmp_path, rows), Thresholds()) == []
    assert len(find_casts(_data(tmp_path, rows), Thresholds(min_cast_duration=10))) == 1


def test_the_rate_and_gaps_are_measured(tmp_path):
    rows = cast(START, rate=4, max_payout=300)
    rows = rows[:400] + rows[440:]  # 10 s missing
    (found,) = find_casts(_data(tmp_path, rows), Thresholds())
    assert found.logging_rate == pytest.approx(4)
    assert found.longest_gap == pytest.approx(10.25)


def test_no_payout_no_casts(tmp_path):
    data = _data(tmp_path, cast(START, max_payout=100))
    data.payout[:] = np.nan
    assert find_casts(data, Thresholds()) == []

"""Idle records dropped as a fileset is read (idle.py): deployments, checks and the tension
envelope as with every record kept, on random cruises built to catch the edges
"""

import dataclasses
from datetime import timedelta

import numpy as np
import pytest
from conftest import CONFIG, START, lci_lines

from winch_data_processing.casts import find_casts
from winch_data_processing.checks import run_checks
from winch_data_processing.config import loads_config
from winch_data_processing.rawdata import read_fileset


def random_cruise(rng: np.random.Generator) -> list[tuple]:
    """(time, tension, payout, speed) rows at 2 Hz: idle stretches, casts (some soaking at a
    steady payout, some close together), a stale counter, a counter reset, payout jitter about
    the threshold, and logger gaps
    """
    rows, t, payout = [], 0.0, 0.0

    def emit(seconds: float, payouts, tension=None) -> None:
        nonlocal t
        n = max(int(seconds * 2), 1)
        values = np.broadcast_to(payouts, n) if np.ndim(payouts) == 0 else payouts
        for i in range(n):
            load = tension if tension is not None else 800 + rng.normal(0, 20) + 0.3 * values[i]
            rows.append((START + timedelta(seconds=t), load, float(values[i]), 0.0))
            t += 0.5

    for _ in range(int(rng.integers(4, 12))):
        kind = rng.choice(["idle", "cast", "cast", "stale", "jitter", "gap"])
        if kind == "idle":
            emit(rng.uniform(5, 3000), payout if payout <= 10 else 0.0)
            payout = rows[-1][2]
        elif kind == "cast":
            depth = rng.uniform(5, 400)
            down = np.linspace(0, depth, max(int(depth / 1.0 * 2), 2))  # 60 m min-1
            emit(len(down) / 2, down)
            if rng.random() < 0.5:
                emit(rng.uniform(10, 900), depth)  # a soak: the payout still
            emit(len(down) / 2, down[::-1].copy())
            payout = 0.0
        elif kind == "stale":
            # Jumped to, not paid out: long enough that a step measured from too far back would
            # look like one the wire could move (664.5 m at 300 m min-1: 133 s)
            emit(rng.uniform(30, 3000), 664.5)
            payout = 664.5
        elif kind == "jitter":
            emit(rng.uniform(5, 200), 10 + rng.choice([-0.1, 0.1], size=10_000)[:400])
            payout = rows[-1][2]
        else:
            t += rng.uniform(1, 1200)  # the logger off
    return rows


def random_config(rng: np.random.Generator) -> str:
    thresholds = {
        "deploy_payout": rng.uniform(5, 20),
        "merge_gap": rng.choice([5.0, 120.0, rng.uniform(10, 1500)]),  # sometimes past 2 paddings
        "min_cast_duration": rng.uniform(0, 300),
        "cast_padding": rng.choice([0.0, 300.0, rng.uniform(1, 700)]),
    }
    table = "\n".join(f"{k} = {float(v)}" for k, v in thresholds.items())
    return f"{CONFIG}\n[thresholds]\n{table}\n"


def write_files(directory, rows, rng: np.random.Generator, in_order: bool = True) -> None:
    """The rows in several files, cut anywhere (in a cast, too), named in time order or not"""
    cuts = sorted(rng.choice(np.arange(1, len(rows)), size=min(5, len(rows) - 1), replace=False))
    parts = np.split(np.array(rows, dtype=object), cuts)
    names = list(range(len(parts))) if in_order else list(reversed(range(len(parts))))
    for name, part in zip(names, parts, strict=True):
        (directory / f"winch-{name:02d}.txt").write_text("\n".join(lci_lines(part)) + "\n")


def assert_as_if_every_record_kept(directory, config) -> None:
    dropped = read_fileset(directory, config)
    full = read_fileset(directory, config, drop_idle=False)
    assert dropped.records == len(full) and len(dropped) <= len(full)
    a, b = find_casts(dropped, config.thresholds), find_casts(full, config.thresholds)

    def figures(cast):
        indices = ("start", "stop", "deployed_start", "deployed_stop")  # into each's records
        return {k: v for k, v in dataclasses.asdict(cast).items() if k not in indices}

    assert [figures(c) for c in a] == [figures(c) for c in b]
    assert run_checks(config, dropped, a) == run_checks(config, full, b)
    for field in ("time", "tension_min", "tension_max"):
        assert np.array_equal(getattr(dropped.envelope, field), getattr(full.envelope, field))


@pytest.mark.parametrize("seed", range(40))
def test_random_cruises_as_if_every_record_kept(tmp_path, seed):
    rng = np.random.default_rng(seed)
    rows = random_cruise(rng)
    write_files(tmp_path, rows, rng)
    assert_as_if_every_record_kept(tmp_path, loads_config(random_config(rng)))


def test_most_records_of_an_idle_cruise_are_dropped(tmp_path):
    """Two hours on deck, a cast to 100 m at 60 m min-1, two more hours, at 2 Hz"""
    deck = [0.0] * 14_400
    payouts = deck + [i / 2 for i in range(1, 201)] + [i / 2 for i in range(200, 0, -1)] + deck
    rows = [
        (START + timedelta(seconds=i / 2), 800.0 + i % 7, p, 0.0) for i, p in enumerate(payouts)
    ]
    write_files(tmp_path, rows, np.random.default_rng(0))
    config = loads_config(CONFIG)
    data = read_fileset(tmp_path, config)
    assert data.records == len(rows) == 29_200
    assert len(data) < 2_000  # the cast's 400 s and 2 x 300 s of padding, at 2 Hz
    (cast,) = find_casts(data, config.thresholds)
    assert cast.max_payout == 100
    # The envelope has every record's tension, a minute at a time
    minutes = np.unique(np.array([r[0] for r in rows], "datetime64[m]"))
    assert np.array_equal(data.envelope.time, minutes.astype("datetime64[ns]"))
    assert data.envelope.tension_max[0] == 806 and data.envelope.tension_min[0] == 800
    assert_as_if_every_record_kept(tmp_path, config)


def test_a_stale_counter_isnt_a_deployment_with_its_records_dropped(tmp_path):
    """A jump to 664.5 m held 20 minutes, then a reset: neither step is one the wire could move,
    so long as each is measured from the record before it, which is kept
    """
    payouts = [0.0] * 2400 + [664.5] * 2400 + [0.0] * 2400  # 2 Hz
    rows = [(START + timedelta(seconds=i / 2), 800.0, p, 0.0) for i, p in enumerate(payouts)]
    write_files(tmp_path, rows, np.random.default_rng(0))
    config = loads_config(CONFIG)
    data = read_fileset(tmp_path, config)
    assert len(data) == 6  # the ends, and either side of the jump and of the reset
    assert find_casts(data, config.thresholds) == []
    assert_as_if_every_record_kept(tmp_path, config)


def test_files_out_of_time_order_keep_every_record(tmp_path):
    rng = np.random.default_rng(1)
    rows = random_cruise(rng)
    write_files(tmp_path, rows, rng, in_order=False)
    config = loads_config(CONFIG)
    data = read_fileset(tmp_path, config)
    assert len(data) == data.records == len(rows)
    assert (np.diff(data.time) >= np.timedelta64(0)).all()
    assert_as_if_every_record_kept(tmp_path, config)


def test_without_a_payout_column_only_the_ends_are_kept(tmp_path):
    lines = ["time,tension"] + [
        f"2026-06-19T12:{i // 60:02d}:{i % 60:02d}Z,{800 + i}" for i in range(600)
    ]
    (tmp_path / "winch.csv").write_text("\n".join(lines) + "\n")
    text = CONFIG.replace('format = "openrvdas_lci90i"', 'format = "delimited"').replace(
        'files = "*.txt"', 'files = "*.csv"\n\n[data.columns]\ntime = "time"\ntension = "tension"'
    )
    config = loads_config(text)
    data = read_fileset(tmp_path, config)
    assert (data.records, len(data), len(data.envelope)) == (600, 2, 10)
    assert data.envelope.tension_max[-1] == 800 + 599
    assert_as_if_every_record_kept(tmp_path, config)

"""The figures (plots.py) and everything a run writes (reports.py), with themes (theme.py)."""

from datetime import timedelta

import numpy as np
import pytest
from conftest import CONFIG, START, cast, write_cruise

from winch_data_processing import plots
from winch_data_processing.assess import assess
from winch_data_processing.cli import main
from winch_data_processing.config import ConfigError, loads_config
from winch_data_processing.reports import report_paths, write_reports
from winch_data_processing.theme import DEFAULT_THEME, resolve_theme

FIGURES = ("timeline", "tension_and_payout", "factor_of_safety")


def two_casts(directory):
    first = cast(START, max_payout=500)
    second = cast(first[-1][0] + timedelta(minutes=30), max_payout=800)
    return write_cruise(directory, first, second)


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return assess(two_casts(tmp_path_factory.mktemp("data")), loads_config(CONFIG))


def test_extremes_keep_every_bins_peak_and_trough():
    rng = np.random.default_rng(0)
    values = rng.normal(size=100_000)
    values[12_345], values[67_890] = 50, -50
    shown = plots.extremes(values, 1000)
    assert len(shown) <= 1000 and (np.diff(shown) > 0).all()
    assert {12_345, 67_890} <= set(shown.tolist())
    assert len(plots.extremes(values[:500], 1000)) == 500  # few enough: all of them


def test_a_still_counter_is_held_up_to_a_gap_and_broken_across_it():
    minute = np.timedelta64(1, "m")
    start = np.datetime64("2026-06-19T00:00", "ns")
    minutes = start + np.array([0, 1, 2, 10, 11]) * minute  # nothing logged 00:03 to 00:10
    times = np.array([start, start + 11 * minute + np.timedelta64(30, "s")])
    payout = np.array([664.5, 0.0])  # held, then zeroed after the gap
    line_times, line = plots._logged_line(times, payout, minutes, times, payout)
    expected = [times[0], start + 3 * minute, start + 3 * minute, start + 10 * minute, times[1]]
    assert np.array_equal(line_times, np.array(expected))
    assert np.array_equal(line, [664.5, 664.5, np.nan, 664.5, 0.0], equal_nan=True)


def test_write_reports_writes_the_log_checks_and_figures(result, tmp_path):
    written = write_reports(result, tmp_path)
    paths = report_paths(result.config, tmp_path)
    svgs = [paths["plots"] / f"{name}.svg" for name in (*FIGURES, "cast_001", "cast_002")]
    assert written == [paths["wire log"], paths["checks"], *svgs]
    assert all(svg.read_text().startswith("<?xml") for svg in svgs)


def test_figures_are_identical_run_to_run_and_stale_casts_go(result, tmp_path):
    first = {p: p.read_bytes() for p in write_reports(result, tmp_path)}
    stale = report_paths(result.config, tmp_path)["plots"] / "cast_003.svg"
    stale.write_text("from a run with three deployments")
    again = {p: p.read_bytes() for p in write_reports(result, tmp_path)}
    assert again == first and not stale.exists()


def test_figures_without_records_payout_or_deployments(tmp_path):
    config = loads_config(CONFIG)
    (tmp_path / "empty").mkdir()
    still = [(START + timedelta(seconds=i), 800.0, 0.0, 0.0) for i in range(600)]
    for directory in (tmp_path / "empty", write_cruise(tmp_path / "still", still)):
        result = assess(directory, config)
        assert not result.casts
        for make in plots.FIGURES.values():
            make(result, DEFAULT_THEME)


def test_the_theme_colours_the_figures(result, tmp_path):
    theme_file = tmp_path / "theme.toml"
    theme_file.write_text('[colors]\nprimary = "#123456"\n')
    figure = plots.timeline(result, resolve_theme(theme_file, result.config))
    svg = tmp_path / "timeline.svg"
    figure.savefig(svg)
    assert "#123456" in svg.read_text()


def test_a_config_theme_applies_over_the_theme_file(tmp_path):
    theme_file = tmp_path / "theme.toml"
    theme_file.write_text('palette = "colorblind"\n[colors]\nprimary = "#123456"\n')
    config = loads_config(f'{CONFIG}\n[theme.colors]\nprimary = "#abcdef"\n')
    theme = resolve_theme(theme_file, config)
    assert theme.colors.primary == "#abcdef" and theme.palette == "colorblind"


def test_a_theme_that_cant_be_used(tmp_path, capsys):
    with pytest.raises(
        ConfigError,
        match=r"unknown key 'theme.colors.tension'; \[theme.colors\] takes primary, secondary",
    ):
        loads_config(f'{CONFIG}\n[theme.colors]\ntension = "#abcdef"\n')
    data = two_casts(tmp_path / "data")
    config = tmp_path / "TEST01_CTD.toml"
    config.write_text(CONFIG)
    theme_file = tmp_path / "theme.toml"
    theme_file.write_text('page_size = "A3"\n')
    command = ["run", str(data), "-c", str(config), "-o", str(tmp_path / "out")]
    assert main([*command, "--theme", str(theme_file)]) == 1
    err = capsys.readouterr().err
    assert (
        err.strip().endswith("page_size must be one of letter, A4") and len(err.splitlines()) == 1
    )
    assert not (tmp_path / "out").exists()  # refused before any work

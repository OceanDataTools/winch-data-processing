"""Figures for the reports. Each takes a `WinchResult` and a `Theme` and returns a matplotlib
Figure:

- `timeline`: the cruise's payout and its tension (each minute's lowest to highest, from every
  record: `WinchData.envelope`) against time, the deployments shaded and numbered, the SWT;
- `tension_and_payout`: all deployments' tension against payout, with the limits;
- `factor_of_safety`: how the deployments' records fall by factor of safety (ABL / tension),
  with the selected FS, the table's alarm point, the lowest FS allowed and the elastic limit;
- `cast_figure`: one deployment's tension and payout against time, with the limits and its
  peak; `cast_figures` makes one for each.

The limits are the config's: the SWT (ABL / the selected FS), the alarm setting (or the table's
alarm point without one), the lowest FS the kind may be operated to, and the elastic limit
(cables and wire rope), all from `rvss.py`. Axis labels and units come from `quantities.py`.
A series with more points than a figure shows is drawn as each bin's lowest and highest values,
in time order, so no peak is lost; the counts shown are always every record's. Lines break where
nothing was logged (no minute in the envelope), not across a stretch the payout held still.
"""

import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
from matplotlib import font_manager, rc_context, rcParams
from matplotlib.figure import Figure
from matplotlib.ticker import NullFormatter

from winch_data_processing import quantities as q
from winch_data_processing import rvss
from winch_data_processing.assess import WinchResult
from winch_data_processing.casts import Cast
from winch_data_processing.theme import DEFAULT_THEME, Theme

FIGURE_WIDTH = 7.5  # [in] US letter less margins; the PDF scales figures to its frame
POINTS = 3000  # most points a line is drawn with; more are reduced to bins' extremes
MAX_CAST_LABELS = 40  # deployments numbered on the timeline; more are shaded only
FS_TOP = 10.0  # the FS histogram's last bin holds everything from here up
FS_BIN = 0.1


def _font_family(path: Path) -> str:
    """matplotlib's name for a TTF, registering it"""
    font_manager.fontManager.addfont(str(path))
    return font_manager.FontProperties(fname=str(path)).get_name()


@contextmanager
def themed(theme: Theme) -> Iterator[None]:
    """matplotlib settings for the theme's fonts, while a figure is made"""
    settings = {}
    if theme.font is not None:
        settings["font.family"] = _font_family(theme.font)
        if theme.bold_font is not None:
            _font_family(theme.bold_font)  # the same family's bold, for bold text
    if theme.figure_font_scale != 1:
        settings["font.size"] = rcParams["font.size"] * theme.figure_font_scale
    with rc_context(settings):
        yield


def _pt(theme: Theme, size: float) -> float:
    """A figure font size [pt] scaled by the theme"""
    return size * theme.figure_font_scale


# -- Data --


def extremes(values: np.ndarray, limit: int = POINTS) -> np.ndarray:
    """Indices of `values` to draw: all of them, or with more than `limit`, each of `limit / 2`
    bins' lowest and highest, in order, so the line keeps every peak and trough
    """
    n = len(values)
    if n <= limit:
        return np.arange(n)
    bins = limit // 2
    starts = np.linspace(0, n, bins + 1).astype(np.intp)[:-1]
    filled = np.where(np.isnan(values), -np.inf, values)
    highest = _bin_arg(filled, starts, np.maximum)
    filled = np.where(np.isnan(values), np.inf, values)
    lowest = _bin_arg(filled, starts, np.minimum)
    return np.unique(np.concatenate((lowest, highest)))


def _bin_arg(values: np.ndarray, starts: np.ndarray, ufunc) -> np.ndarray:
    """Each bin's index of its `ufunc` (np.maximum or np.minimum) value"""
    best = ufunc.reduceat(values, starts)
    ends = np.append(starts[1:], len(values))
    bin_of = np.repeat(np.arange(len(starts)), ends - starts)
    hits = np.flatnonzero(values == best[bin_of])
    first = np.unique(bin_of[hits], return_index=True)[1]
    return hits[first]


def _with_breaks(times: np.ndarray, *columns: np.ndarray, at: np.ndarray):
    """The points with a NaN value after each index in `at` (at that point's time), so a line
    breaks there
    """
    if not len(at):
        return (times, *columns)
    positions = at + 1
    times = np.insert(times, positions, times[at])
    return (times, *(np.insert(c.astype(float), positions, np.nan) for c in columns))


def _logged_line(
    times: np.ndarray, values: np.ndarray, minutes: np.ndarray, every_time, every_value
) -> tuple[np.ndarray, np.ndarray]:
    """The line through the points, broken where nothing was logged (no minute in the
    envelope's `minutes`) and each side held at the value the records (`every_time`,
    `every_value`) had before it: a counter still for days is a line up to the gap
    """
    if len(minutes) < 2 or not len(times):
        return times, values
    gaps = np.flatnonzero(np.diff(minutes) > np.timedelta64(1, "m"))
    if not len(gaps):
        return times, values
    starts = minutes[gaps] + np.timedelta64(1, "m")  # the end of the last minute logged
    ends = minutes[gaps + 1]
    before = np.searchsorted(every_time, starts, side="right") - 1
    held = np.where(before >= 0, every_value[np.maximum(before, 0)], np.nan)
    # In this order, so a stable sort puts each gap's end before a record at the same time,
    # and its held start before its break
    merged_times = np.concatenate((ends, times, starts, starts))
    merged = np.concatenate((held, values.astype(float), held, np.full(len(starts), np.nan)))
    order = np.argsort(merged_times, kind="stable")
    return merged_times[order], merged[order]


def _count(n: int, thing: str) -> str:
    return f"{n:,} {thing}" if n == 1 else f"{n:,} {thing}s"


def _resets(times: np.ndarray, payout: np.ndarray, max_speed: float) -> np.ndarray:
    """Indices i where the payout steps from point i to i + 1 faster than `max_speed`
    [m min-1] (at least 1 m) moves the wire, as `casts.find_casts` tells a counter reset
    """
    seconds = np.diff(times) / np.timedelta64(1, "s")
    with np.errstate(invalid="ignore"):
        return np.flatnonzero(np.abs(np.diff(payout)) > np.maximum(max_speed / 60 * seconds, 1.0))


def _limits(result: WinchResult) -> list[tuple[str, float, str]]:
    """(label, tension [lbf], line style) of the config's limits"""
    member = result.config.tension_member
    abl, table = member.assigned_breaking_load, member.table
    limits = [(f"SWT, FS {member.factor_of_safety:g}", member.safe_working_tension, "-")]
    alarm = result.config.monitoring.alarm_tension
    if alarm is not None:
        limits.append((f"Alarm setting, FS {abl / alarm:.2f}", alarm, "--"))
    elif table.alarm_fs is not None:
        limits.append((f"Alarm point, FS {table.alarm_fs:g}", abl / table.alarm_fs, "--"))
    lowest = rvss.MIN_FS[member.kind]
    limits.append((f"Lowest FS allowed, {lowest:g}", abl / lowest, "-."))
    if member.kind in rvss.ELASTIC_LIMIT_FS:
        elastic = rvss.ELASTIC_LIMIT_FS[member.kind]
        limits.append((f"Elastic limit, FS {elastic:g}", abl / elastic, ":"))
    return limits


def _draw_limits(axis, result: WinchResult, theme: Theme, top: float | None = None) -> None:
    """The limits as horizontal lines, those within the axis's range or up to `top`"""
    for label, tension, style in _limits(result):
        if top is None or tension <= top:
            axis.axhline(tension, color=theme.colors.secondary, linestyle=style, lw=1, label=label)


def _date_axis(axis) -> None:
    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def _no_data(figure: Figure, axis, text: str, theme: Theme) -> Figure:
    axis.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        transform=axis.transAxes,
        color=theme.colors.muted_text,
        fontsize=_pt(theme, 10),
    )
    axis.set_xticks([])
    axis.set_yticks([])
    return figure


# -- Figures --


def timeline(result: WinchResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """The cruise's payout and tension against time, the deployments shaded"""
    data, envelope = result.data, result.data.envelope
    with themed(theme):
        figure = Figure(figsize=(FIGURE_WIDTH, 4.5), layout="constrained")
        payout_axis, tension_axis = figure.subplots(2, 1, sharex=True)
        if not len(data):
            return _no_data(figure, tension_axis, "No records", theme)
        minutes = envelope.time
        if data.has_payout:
            shown = extremes(data.payout)
            times, payout = data.time[shown], data.payout[shown]
            times, payout = _logged_line(times, payout, minutes, data.time, data.payout)
            payout_axis.plot(times, payout, color=theme.colors.muted_text, lw=0.8)
        else:
            _no_data(figure, payout_axis, "No payout in the data", theme)
        payout_axis.invert_yaxis()
        payout_axis.set_ylabel(q.PAYOUT.label)
        low, high = _binned_envelope(minutes, envelope.tension_min, envelope.tension_max)
        breaks = np.flatnonzero(np.diff(low[0]) > np.timedelta64(int(_bin_minutes(minutes)), "m"))
        times, lows, highs = _with_breaks(low[0], low[1], high[1], at=breaks)
        tension_axis.fill_between(
            times,
            lows,
            highs,
            color=theme.colors.primary,
            lw=0.6,
            edgecolor=theme.colors.primary,
            label="Tension, each minute's range",
        )
        member = result.config.tension_member
        tension_axis.axhline(
            member.safe_working_tension,
            color=theme.colors.secondary,
            lw=1,
            label=f"SWT, FS {member.factor_of_safety:g}",
        )
        tension_axis.set_ylabel(q.TENSION.label)
        tension_axis.legend(loc="best", fontsize=_pt(theme, 7))
        for cast in result.casts:
            for axis in (payout_axis, tension_axis):
                axis.axvspan(
                    cast.start_time, cast.end_time, color=theme.colors.status_info, zorder=0, lw=0
                )
            if len(result.casts) <= MAX_CAST_LABELS:
                payout_axis.annotate(
                    str(cast.number),
                    (cast.start_time, 1),
                    xycoords=("data", "axes fraction"),
                    va="bottom",
                    fontsize=_pt(theme, 6),
                    color=theme.colors.muted_text,
                )
        _date_axis(tension_axis)
        title = (
            f"{result.config.cruise_id}: {_count(data.records, 'record')}, "
            f"{_count(len(result.casts), 'deployment')}"
        )
        payout_axis.set_title(title, loc="left", fontsize=_pt(theme, 9), pad=12)
    return figure


def _bin_minutes(minutes: np.ndarray) -> float:
    """Minutes a bin of the timeline's tension spans: 1, or more for a long cruise"""
    if len(minutes) < 2:
        return 1.0
    span = (minutes[-1] - minutes[0]) / np.timedelta64(1, "m")
    return max(1.0, np.ceil(span / (POINTS // 2)))


def _binned_envelope(minutes, lows, highs):
    """The envelope in bins of `_bin_minutes`, each the lowest low and highest high:
    ((times, lows), (times, highs))
    """
    width = _bin_minutes(minutes)
    if width == 1 or not len(minutes):
        return (minutes, lows), (minutes, highs)
    bins = ((minutes - minutes[0]) / np.timedelta64(1, "m") // width).astype(np.int64)
    starts = np.flatnonzero(np.concatenate(([True], bins[1:] != bins[:-1])))
    times = minutes[starts]
    return (times, np.minimum.reduceat(lows, starts)), (times, np.maximum.reduceat(highs, starts))


def _cast_records(result: WinchResult, cast: Cast) -> slice:
    return slice(cast.start, cast.stop)


def tension_and_payout(result: WinchResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """Every deployment's tension against its payout, with the limits"""
    data = result.data
    with themed(theme):
        figure = Figure(figsize=(FIGURE_WIDTH, 4.0), layout="constrained")
        axis = figure.subplots()
        if not result.casts:
            return _no_data(figure, axis, "No deployments", theme)
        cycle = theme.cycle
        top = 0.0
        per_cast = max(POINTS // len(result.casts), 200)
        for i, cast in enumerate(result.casts):
            records = _cast_records(result, cast)
            tension, payout = data.tension[records], data.payout[records]
            shown = extremes(tension, per_cast)
            label = f"{cast.number}" if len(result.casts) <= len(cycle) else None
            # Broken at a step faster than the wire moves (a counter reset), which would
            # otherwise draw a line across the figure
            times = data.time[records][shown]
            resets = _resets(times, payout[shown], result.config.thresholds.max_wire_speed)
            _, x, y = _with_breaks(times, payout[shown], tension[shown], at=resets)
            axis.plot(x, y, color=cycle[i % len(cycle)], lw=0.6, label=label)
            top = max(top, cast.max_tension)
        _draw_limits(
            axis,
            result,
            theme,
            top=max(top * 1.3, result.config.tension_member.safe_working_tension),
        )
        axis.set_xlabel(q.PAYOUT.label)
        axis.set_ylabel(q.TENSION.label)
        axis.legend(
            loc="upper left",
            fontsize=_pt(theme, 7),
            ncols=2,
            title="Deployment",
            title_fontsize=_pt(theme, 7),
        )
    return figure


def factor_of_safety(result: WinchResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """The deployments' records by factor of safety, with the FS limits"""
    data, member = result.data, result.config.tension_member
    with themed(theme):
        figure = Figure(figsize=(FIGURE_WIDTH, 3.2), layout="constrained")
        axis = figure.subplots()
        if not result.casts:
            return _no_data(figure, axis, "No deployments", theme)
        tension = np.concatenate([data.tension[_cast_records(result, c)] for c in result.casts])
        tension = tension[tension > 0]
        fs = np.minimum(member.assigned_breaking_load / tension, FS_TOP)
        lowest = min(1.0, float(np.floor(fs.min() * 10) / 10)) if len(fs) else 1.0
        edges = np.arange(lowest, FS_TOP + FS_BIN * 1.5, FS_BIN)
        axis.hist(fs, bins=edges, color=theme.colors.primary)
        axis.set_yscale("log")
        axis.set_ylim(bottom=0.8)
        axis.yaxis.set_minor_formatter(NullFormatter())
        lines = [(f"Selected, {member.factor_of_safety:g}", member.factor_of_safety, "-")]
        if member.table.alarm_fs is not None:
            lines.append((f"Alarm point, {member.table.alarm_fs:g}", member.table.alarm_fs, "--"))
        lines.append(
            (f"Lowest allowed, {rvss.MIN_FS[member.kind]:g}", rvss.MIN_FS[member.kind], "-.")
        )
        if member.kind in rvss.ELASTIC_LIMIT_FS:
            elastic = rvss.ELASTIC_LIMIT_FS[member.kind]
            lines.append((f"Elastic limit, {elastic:g}", elastic, ":"))
        for label, value, style in lines:
            axis.axvline(value, color=theme.colors.secondary, linestyle=style, lw=1, label=label)
        axis.set_xlim(lowest, FS_TOP + FS_BIN)
        axis.set_xlabel(
            f"{q.FACTOR_OF_SAFETY.label} (ABL / tension; the last bin {FS_TOP:g} and up)"
        )
        axis.set_ylabel(f"{q.COUNT.label} of records")
        axis.legend(loc="upper left", fontsize=_pt(theme, 7))
        axis.set_title(
            f"{_count(len(tension), 'record')} with tension, in "
            f"{_count(len(result.casts), 'deployment')}",
            loc="left",
            fontsize=_pt(theme, 9),
        )
    return figure


def cast_figure(result: WinchResult, cast: Cast, theme: Theme = DEFAULT_THEME) -> Figure:
    """One deployment's tension and payout against time, the limits and its peak"""
    data = result.data
    records = _cast_records(result, cast)
    times, tension, payout = data.time[records], data.tension[records], data.payout[records]
    with themed(theme):
        figure = Figure(figsize=(FIGURE_WIDTH, 3.2), layout="constrained")
        axis = figure.subplots()
        shown = extremes(tension)
        axis.plot(
            times[shown], tension[shown], color=theme.colors.primary, lw=0.7, label="Tension"
        )
        _draw_limits(axis, result, theme, top=max(cast.max_tension * 1.3, 1.0))
        axis.plot(
            [cast.max_tension_time],
            [cast.max_tension],
            "v",
            color=theme.colors.secondary,
            label=f"Peak, {q.MAX_TENSION.format(cast.max_tension)} {q.TENSION.unit}",
        )
        axis.set_ylabel(q.TENSION.label)
        right = axis.twinx()
        shown = extremes(payout)
        right.plot(
            times[shown], payout[shown], color=theme.colors.muted_text, lw=0.7, label="Payout"
        )
        right.invert_yaxis()
        right.set_ylabel(q.PAYOUT.label)
        _date_axis(axis)
        handles = axis.get_legend_handles_labels()
        more = right.get_legend_handles_labels()
        # Below the axes: a deployment's peaks are often at its ends, where a legend would go
        figure.legend(
            handles[0] + more[0],
            handles[1] + more[1],
            loc="outside lower center",
            ncols=3,
            fontsize=_pt(theme, 7),
            frameon=False,
        )
        fs = result.config.tension_member.assigned_breaking_load / cast.max_tension
        title = (
            f"Deployment {cast.number}: {q.format_time(cast.start_time, 0)} to "
            f"{q.format_time(cast.end_time, 0)}, {q.MAX_PAYOUT.format(cast.max_payout)} m out, "
            f"peak {q.MAX_TENSION.format(cast.max_tension)} lbf (FS {fs:.2f})"
            if cast.max_tension > 0
            else f"Deployment {cast.number}"
        )
        axis.set_title("\n".join(textwrap.wrap(title, 110)), loc="left", fontsize=_pt(theme, 9))
    return figure


def cast_figures(
    result: WinchResult, theme: Theme = DEFAULT_THEME
) -> Iterator[tuple[str, Figure]]:
    """(name, figure) for each deployment: cast_001, cast_002, ..."""
    for cast in result.casts:
        yield f"cast_{cast.number:03d}", cast_figure(result, cast, theme)


# The whole cruise's figures, by name, in the reports' order; the deployments' follow
FIGURES = {
    "timeline": timeline,
    "tension_and_payout": tension_and_payout,
    "factor_of_safety": factor_of_safety,
}

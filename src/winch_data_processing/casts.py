"""Deployments (casts) found in the payout, and each one's figures for the wire log.

Appendix A asks for each deployment's maximum tension, the line out at that time, and its
maximum payout (A.5.2.1, A.5.3.1, A.8.3.6), so a deployment has to be found in the data. The
rule, inferred (Appendix A doesn't define a deployment's extent):
- The tension member is deployed while the payout exceeds `deploy_payout` (10 m).
- A stretch is a deployment only if the payout crosses `deploy_payout` into it and out of it:
  from a record at or below it, by a step the wire could have moved at `max_wire_speed`
  (300 m min-1). A counter left at a stale value isn't: RR2403's hydro winch read 664.5 m,
  idle, from the cruise's start until the counter was zeroed (a 664 m step in 50 ms) before
  its one cast. Nor, so, is a deployment under way when the data starts or ends.
- Dips below it shorter than `merge_gap` (120 s) are part of the same deployment: a package
  soaking near the surface, or a payout counter's jitter.
- Deployments shorter than `min_cast_duration` (60 s) are dropped: spooling, testing.
- Each is padded by `cast_padding` (300 s) either side, but never past the midpoint to the next,
  so the lift off the deck and over the side, and the recovery, often a shallow deployment's
  highest tensions, are counted with it, even across a counter reset (RR2403's lift, 2,564
  lbf, came before the counter was zeroed). The maximum payout is the deployed stretch's, not
  the padding's.
"""

from dataclasses import dataclass

import numpy as np

from winch_data_processing.config import Thresholds
from winch_data_processing.rawdata import WinchData


@dataclass(frozen=True)
class Cast:
    """One deployment: its records `start` to `stop` (exclusive) in `WinchData`, and its
    figures
    """

    number: int
    start: int
    stop: int
    start_time: np.datetime64
    end_time: np.datetime64
    max_payout: float  # [m]
    max_tension: float  # [lbf]
    max_tension_time: np.datetime64
    payout_at_max_tension: float  # [m]
    samples: int
    logging_rate: float  # [Hz], the median interval's
    longest_gap: float  # [s]

    @property
    def duration(self) -> float:  # [s]
        return (self.end_time - self.start_time) / np.timedelta64(1, "s")


def find_casts(data: WinchData, thresholds: Thresholds) -> list[Cast]:
    if not len(data) or not data.has_payout:
        return []
    seconds = (data.time - data.time[0]) / np.timedelta64(1, "s")
    deployed = np.nan_to_num(data.payout, nan=-np.inf) > thresholds.deploy_payout
    step = _plausible_steps(data.payout, seconds, thresholds.max_wire_speed)
    runs = [
        (a, b)
        for a, b in _runs(deployed)
        if a > 0 and b < len(deployed) and step[a - 1] and step[b - 1]  # crossed in and out
    ]
    runs = _merge(runs, seconds, thresholds.merge_gap)
    runs = [(a, b) for a, b in runs if seconds[b - 1] - seconds[a] >= thresholds.min_cast_duration]
    padded = _pad(runs, seconds, thresholds.cast_padding)
    return [
        _cast(data, seconds, number, run, around)
        for number, (run, around) in enumerate(zip(runs, padded, strict=True), 1)
    ]


def _plausible_steps(payout, seconds, max_speed: float) -> np.ndarray:
    """Whether each step, record i to i + 1, is one the wire could move at `max_speed`
    [m min-1] (at least 1 m, for the counter's resolution and jitter)
    """
    limit = np.maximum(max_speed / 60 * np.diff(seconds), 1.0)
    with np.errstate(invalid="ignore"):
        return np.abs(np.diff(payout)) <= limit


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """The (start, stop) index ranges where `mask` is True"""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def _merge(runs, seconds, gap) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, stop in runs:
        if merged and seconds[start] - seconds[merged[-1][1] - 1] < gap:
            merged[-1] = (merged[-1][0], stop)
        else:
            merged.append((start, stop))
    return merged


def _pad(runs, seconds, padding) -> list[tuple[int, int]]:
    """Each run widened by `padding` [s], up to the midpoint to its neighbours"""
    padded = []
    for i, (start, stop) in enumerate(runs):
        earliest = seconds[start] - padding
        latest = seconds[stop - 1] + padding
        if i:
            earliest = max(earliest, (seconds[runs[i - 1][1] - 1] + seconds[start]) / 2)
        if i + 1 < len(runs):
            latest = min(latest, (seconds[stop - 1] + seconds[runs[i + 1][0]]) / 2)
        padded.append(
            (
                int(np.searchsorted(seconds, earliest, side="left")),
                int(np.searchsorted(seconds, latest, side="right")),
            )
        )
    return padded


def _cast(data: WinchData, seconds, number: int, run, around) -> Cast:
    """A cast's figures: its payout from the deployed stretch `run`, the rest from the padded
    `around` (index ranges)
    """
    start, stop = around
    tension = data.tension[start:stop]
    payout = data.payout[run[0] : run[1]]
    peak = start + int(np.nanargmax(tension))
    steps = np.diff(seconds[start:stop])
    interval = float(np.median(steps)) if len(steps) else np.nan
    return Cast(
        number=number,
        start=start,
        stop=stop,
        start_time=data.time[start],
        end_time=data.time[stop - 1],
        max_payout=float(np.nanmax(payout)),
        max_tension=float(data.tension[peak]),
        max_tension_time=data.time[peak],
        payout_at_max_tension=float(data.payout[peak]),
        samples=stop - start,
        logging_rate=1 / interval if interval > 0 else np.nan,
        longest_gap=float(steps.max()) if len(steps) else np.nan,
    )

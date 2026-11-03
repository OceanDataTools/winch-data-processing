"""Idle records dropped as a fileset is read, keeping what finding deployments needs.

Most of a cruise's records are idle: the package on deck, the counter still. `casts.find_casts`
needs, outside its deployments' records, only these, so they're kept and the rest dropped:
- every record within `window` (the larger of `cast_padding` and `merge_gap`, and a second) of
  a plausible payout crossing, the step into or out of payout over `deploy_payout` that
  `find_casts` accepts: a deployment's padding (its lift and recovery) and a merge's dip whole;
- every record of a deployed run entered by such a crossing: a soak at a steady payout;
- where the payout changes: either side of each change, and the first and last records, so
  each crossing, step and run is where it was, at its own records' times.
`find_casts` then finds the same deployments, with the same figures, its indices into the kept
records. A crossing is counted plausible here with a little slack, so the kept records are
never fewer than it needs. RR2403's hydro winch: 6.57 M records, about 1 % kept.

The cruise's tension minute by minute (`Envelope`: each minute's minimum and maximum) is kept
from every record, for plots of the whole cruise: about 0.1 % of the records at 20 Hz.

Records are settled as files are read, in time order: those more than `window` before the newest
record are out of reach of a crossing still to come, so they're kept or dropped then. A run then
holds the kept records and about a file's. A file that starts before what's settled
(`OutOfOrder`) means the fileset isn't in time order by file; `rawdata` reads it again without
dropping any.
"""

from dataclasses import dataclass

import numpy as np

from winch_data_processing.config import Thresholds
from winch_data_processing.formats import Columns

MINUTE = 60 * 10**9  # [ns]


@dataclass
class Envelope:
    """Tension, minute by minute, from every record"""

    time: np.ndarray  # datetime64[ns], each minute's start
    tension_min: np.ndarray  # [lbf]
    tension_max: np.ndarray  # [lbf]

    def __len__(self) -> int:
        return len(self.time)


class OutOfOrder(Exception):
    """A file's records start before records already settled"""


@dataclass
class _Last:
    """The last settled record, the one before the records still to settle"""

    time: int  # [ns]
    payout: float  # [m]
    deployed: bool
    entered: bool  # into a deployed run by a plausible crossing


class Settler:
    """Settles the records written into `out`, file by file: keeps what `find_casts` needs"""

    def __init__(self, out: Columns, thresholds: Thresholds):
        self.out = out
        self.deploy_payout = thresholds.deploy_payout
        self.max_wire_speed = thresholds.max_wire_speed
        window = max(thresholds.cast_padding, thresholds.merge_gap) + 1  # [s]
        self.window = int(window * 10**9)  # [ns]
        self.settled = 0  # records at the start of `out` that are final
        self.last: _Last | None = None
        self.keep_until = np.iinfo(np.int64).min  # [ns] a settled crossing's window's end
        self.envelope: list[tuple[np.ndarray, ...]] = []

    def add(self, start: int) -> None:
        """A file's records, `out` from `start`: put in time order with the unsettled ones,
        then settled as far as they can be
        """
        time = self.out.time[start : self.out.size].view(np.int64)
        if not len(time):
            return
        if self.last is not None and time.min() < self.last.time:
            raise OutOfOrder
        unsettled = slice(self.settled, self.out.size)
        times = self.out.time[unsettled]
        if not (times[1:] >= times[:-1]).all():
            order = np.argsort(times, kind="stable")
            for column in self._columns():
                column[unsettled] = column[unsettled][order]
        self._settle(final=False)

    def finish(self) -> Envelope:
        """Settles the rest; the envelope of every record"""
        self._settle(final=True)
        return _joined(self.envelope)

    def _columns(self) -> tuple[np.ndarray, ...]:
        out = self.out
        return out.time, out.tension, out.payout, out.speed

    def _settle(self, final: bool) -> None:
        start, size = self.settled, self.out.size
        time = self.out.time[start:size].view(np.int64)
        if not len(time):
            return
        # The last record waits for the next, and every record a crossing to come could reach
        cut = len(time) if final else int(np.searchsorted(time, time[-1] - self.window))
        if not cut:
            return
        keep, last = self._keep(time, self.out.payout[start:size], cut, final)
        _add_minutes(self.envelope, time[:cut], self.out.tension[start : start + cut])
        kept = np.flatnonzero(keep)
        for column in self._columns():
            records = column[start:size]
            rest = records[cut:].copy()
            records[: len(kept)] = records[kept]
            records[len(kept) : len(kept) + len(rest)] = rest
        self.settled = start + len(kept)
        self.out.size = self.settled + len(time) - cut
        self.last = last

    def _keep(self, time, payout, cut: int, final: bool) -> tuple[np.ndarray, _Last]:
        """Which of the first `cut` records to keep, from all the unsettled ones; and the last
        of those `cut`, for the next settling
        """
        last, n = self.last, len(time)
        deployed = payout > self.deploy_payout  # NaN isn't
        # Where the payout changes (NaN to NaN doesn't), either side
        changed = np.ones(n, dtype=bool)
        changed[1:] = _differ(payout[1:], payout[:-1])
        if last is not None:
            changed[0] = _differ(payout[:1], np.array([last.payout]))[0]
        keep = changed.copy()
        keep[:-1] |= changed[1:]
        keep[-1] |= final
        # Crossings: deployed differs from the record before, by a plausible step
        flip = np.zeros(n, dtype=bool)
        flip[1:] = deployed[1:] != deployed[:-1]
        if last is not None:
            flip[0] = deployed[0] != last.deployed
        flips = np.flatnonzero(flip)
        before_time = np.where(flips > 0, time[flips - 1], last.time if last else 0)
        before_payout = np.where(flips > 0, payout[flips - 1], last.payout if last else np.nan)
        plausible = _plausible(
            payout[flips] - before_payout, (time[flips] - before_time) / 1e9, self.max_wire_speed
        )
        crossings = time[flips[plausible]]
        keep |= time <= self.keep_until
        keep |= _within(time, crossings, self.window)
        # Deployed runs entered by a plausible crossing; run 0 is the one under way before
        entered = np.concatenate(([last is not None and last.entered], plausible))
        run = np.cumsum(flip)
        keep |= deployed & entered[run]
        if len(crossings):
            self.keep_until = max(self.keep_until, int(crossings[-1]) + self.window)
        c = cut - 1
        return keep[:cut], _Last(
            int(time[c]),
            float(payout[c]),
            bool(deployed[c]),
            bool(deployed[c] and entered[run[c]]),
        )


def envelope(time: np.ndarray, tension: np.ndarray) -> Envelope:
    """Tension minute by minute from records in time order (when none are dropped)"""
    parts: list[tuple[np.ndarray, ...]] = []
    _add_minutes(parts, time.view(np.int64), tension)
    return _joined(parts)


def _add_minutes(parts: list, time: np.ndarray, tension: np.ndarray) -> None:
    """Each minute's lowest and highest tension, into `parts`; a minute begun in the last part
    is joined to it
    """
    if not len(time):
        return
    minutes = time // MINUTE
    starts = np.flatnonzero(np.concatenate(([True], minutes[1:] != minutes[:-1])))
    part = (
        minutes[starts],
        np.minimum.reduceat(tension, starts),
        np.maximum.reduceat(tension, starts),
    )
    if parts and parts[-1][0][-1] == part[0][0]:
        before = parts[-1]
        before[1][-1] = min(before[1][-1], part[1][0])
        before[2][-1] = max(before[2][-1], part[2][0])
        part = tuple(p[1:] for p in part)
    if len(part[0]):
        parts.append(part)


def _joined(parts: list) -> Envelope:
    if not parts:
        empty = np.array([], dtype=float)
        return Envelope(np.array([], "datetime64[ns]"), empty, empty)
    minutes, lows, highs = (np.concatenate(c) for c in zip(*parts, strict=True))
    return Envelope((minutes * MINUTE).astype("datetime64[ns]"), lows, highs)


def _differ(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a != b, but NaN equals NaN"""
    return (a != b) & ~(np.isnan(a) & np.isnan(b))


def _plausible(step: np.ndarray, seconds: np.ndarray, max_speed: float) -> np.ndarray:
    """Steps the wire could move at `max_speed` [m min-1] (at least 1 m), as `find_casts`
    tests them, with slack for its seconds' rounding: never fewer
    """
    limit = np.maximum(max_speed / 60 * seconds, 1.0) * (1 + 1e-9) + 1e-9
    with np.errstate(invalid="ignore"):
        return np.abs(step) <= limit


def _within(time: np.ndarray, centres: np.ndarray, window: int) -> np.ndarray:
    """Which times are within `window` [ns] of a centre (both in time order)"""
    marks = np.zeros(len(time) + 1, dtype=np.int64)
    np.add.at(marks, np.searchsorted(time, centres - window), 1)
    np.add.at(marks, np.searchsorted(time, centres + window, side="right"), -1)
    return np.cumsum(marks[:-1]) > 0

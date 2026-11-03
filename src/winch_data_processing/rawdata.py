"""A winch's raw files, read into one time series in the project's units.

`read_fileset(directory, config)` finds the files by `[data] files`, reads each with the
format's reader, converts tension, payout and speed (`units.py`), keeps the cruise's dates when
the config has them, and sorts by time. A file that can't be read is recorded, not raised, so
one bad file never stops a cruise; lines a reader couldn't use are counted by reason.
"""

import gzip
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np

from winch_data_processing import formats, units
from winch_data_processing.config import Config

logger = logging.getLogger(__name__)


@dataclass
class WinchData:
    """The cruise's winch records, sorted by time"""

    time: np.ndarray  # datetime64[ns], UTC
    tension: np.ndarray  # [lbf]
    payout: np.ndarray  # [m]; NaN without a payout column
    speed: np.ndarray  # [m min-1]
    files: list[Path] = field(default_factory=list)  # read, in name order
    errors: list[str] = field(default_factory=list)  # files that couldn't be read, and why
    skipped: dict[str, Counter] = field(default_factory=dict)  # by file name, then reason
    names: Counter = field(default_factory=Counter)  # records by winch name, all files
    outside: int = 0  # records outside the cruise's dates
    backwards: int = 0  # records whose time went back from the one before, as logged

    def __len__(self) -> int:
        return len(self.time)

    @property
    def has_payout(self) -> bool:
        return bool(np.isfinite(self.payout).any())


def find_files(directory: Path, pattern: str) -> list[Path]:
    return sorted(p for p in Path(directory).glob(pattern) if p.is_file())


def read_fileset(directory: Path | str, config: Config) -> WinchData:
    source = config.data
    reader = formats.READERS[source.format]
    parts, files, errors, skipped, names = [], [], [], {}, Counter()
    backwards = 0
    for path in find_files(Path(directory), source.files):
        try:
            with _open(path) as lines:
                found = reader.read(lines, source, config.record_name)
        except (OSError, UnicodeDecodeError, EOFError) as error:
            errors.append(f"{path.name}: {error}")
            continue
        files.append(path)
        names.update(found.names)
        if found.skipped:
            skipped[path.name] = found.skipped
        if len(found):
            backwards += int(np.count_nonzero(np.diff(found.time) < np.timedelta64(0)))
            parts.append(found)
        logger.info("%s: %d records", path.name, len(found))
    if not parts:
        empty = np.array([], dtype=float)
        return WinchData(
            np.array([], "datetime64[ns]"), empty, empty, empty, files, errors, skipped, names
        )
    time = np.concatenate([p.time for p in parts])
    tension = units.convert(
        np.concatenate([p.tension for p in parts]), source.tension_unit, units.TENSION, "tension"
    )
    payout = units.convert(
        np.concatenate([p.payout for p in parts]), source.payout_unit, units.LENGTH, "payout"
    )
    speed = units.convert(
        np.concatenate([p.speed for p in parts]), source.speed_unit, units.SPEED, "speed"
    )
    keep = _within(time, config)
    outside = int(np.count_nonzero(~keep))
    order = np.argsort(time[keep], kind="stable")
    return WinchData(
        time[keep][order],
        tension[keep][order],
        payout[keep][order],
        speed[keep][order],
        files,
        errors,
        skipped,
        names,
        outside,
        backwards,
    )


def _within(time: np.ndarray, config: Config) -> np.ndarray:
    """The records within the cruise's dates, whole UTC days"""
    keep = np.ones(len(time), dtype=bool)
    if config.cruise_start is not None:
        keep &= time >= np.datetime64(config.cruise_start, "ns")
    if config.cruise_end is not None:
        keep &= time < np.datetime64(config.cruise_end + timedelta(days=1), "ns")
    return keep


def _open(path: Path):
    """A text file's lines, gzip-compressed or not, decoded as ASCII with bad bytes replaced"""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="ascii", errors="replace", newline=None)
    return open(path, encoding="ascii", errors="replace", newline=None)

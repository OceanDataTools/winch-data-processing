"""A winch's raw files, read into one time series in the project's units.

`read_fileset(directory, config)` finds the files by `[data] files`, reads each with the
format's reader, converts tension, payout and speed (`units.py`), keeps the cruise's dates when
the config has them, and sorts by time. A file that can't be read is recorded, not raised, so
one bad file never stops a cruise; lines a reader couldn't use are counted by reason.

Memory: the files' records are written into one set of columns (`formats.Columns`), sized by
the fileset's line count, so a run holds its records, 32 bytes each, plus a block's work: no
per-file arrays, no joining them. Records are copied again only to drop those outside the
cruise or to sort, a column at a time.
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
    paths = find_files(Path(directory), source.files)
    out = formats.Columns(sum(_lines(path) for path in paths))
    files, errors, skipped, names = [], [], {}, Counter()
    backwards = 0
    for path in paths:
        start = out.size
        try:
            if reader.read_file is not None:
                with _open_bytes(path) as stream:
                    found = reader.read_file(stream, source, config.record_name, out)
            else:
                with _open(path) as lines:
                    found = reader.read(lines, source, config.record_name)
                out.extend(found.time, found.tension, found.payout, found.speed)
        except (OSError, UnicodeDecodeError, EOFError) as error:
            out.size = start  # what it wrote before failing isn't kept
            errors.append(f"{path.name}: {error}")
            continue
        files.append(path)
        names.update(found.names)
        if found.skipped:
            skipped[path.name] = found.skipped
        time = out.time[start : out.size]
        backwards += int(np.count_nonzero(time[1:] < time[:-1]))
        logger.info("%s: %d records", path.name, out.size - start)
    # Copied only to drop records outside the cruise or to sort, a column at a time
    time = out.time[: out.size]
    keep = _within(time, config)
    outside = int(np.count_nonzero(~keep))
    keep = keep if outside else None
    if keep is not None:
        time = time[keep]
    order = None if _sorted(time) else np.argsort(time, kind="stable")
    if order is not None:
        time = time[order]
    columns = []
    for name, table in (
        ("tension", units.TENSION),
        ("payout", units.LENGTH),
        ("speed", units.SPEED),
    ):
        values = getattr(out, name)[: out.size]
        if keep is not None:
            values = values[keep]
        if order is not None:
            values = values[order]
        columns.append(units.convert(values, getattr(source, f"{name}_unit"), table, name))
    return WinchData(time, *columns, files, errors, skipped, names, outside, backwards)


def _lines(path: Path) -> int:
    """A file's line count, at least its records' (0 if it can't be read: reading says why)"""
    lines, last = 0, b"\n"
    try:
        with _open_bytes(path) as stream:
            while block := stream.read(1 << 22):
                lines += block.count(b"\n")
                last = block[-1:]
    except (OSError, EOFError):
        return 0
    return lines + (last != b"\n")


def _sorted(time: np.ndarray) -> bool:
    return bool((time[1:] >= time[:-1]).all())


def _within(time: np.ndarray, config: Config) -> np.ndarray:
    """The records within the cruise's dates, whole UTC days"""
    keep = np.ones(len(time), dtype=bool)
    if config.cruise_start is not None:
        keep &= time >= np.datetime64(config.cruise_start, "ns")
    if config.cruise_end is not None:
        keep &= time < np.datetime64(config.cruise_end + timedelta(days=1), "ns")
    return keep


def _open_bytes(path: Path):
    """A file's bytes, gzip-compressed or not"""
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def _open(path: Path):
    """A text file's lines, gzip-compressed or not, decoded as ASCII with bad bytes replaced"""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="ascii", errors="replace", newline=None)
    return open(path, encoding="ascii", errors="replace", newline=None)

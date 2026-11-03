"""A winch's raw files, read into one time series in the project's units.

`read_fileset(directory, config)` finds the files by `[data] files`, reads each with the
format's reader, converts tension, payout and speed (`units.py`), keeps the cruise's dates when
the config has them, sorts by time, and drops the idle records `find_casts` doesn't need
(`idle.py`), keeping the tension minute by minute. A file that can't be read is recorded, not
raised, so one bad file never stops a cruise; lines a reader couldn't use are counted by reason.

Memory: the files' records are written into one set of columns (`formats.Columns`), sized by
the fileset's line count, and settled file by file, so a run holds the kept records and about a
file's, 32 bytes each: no per-file arrays, no joining them. Files that aren't in time order
are read again keeping every record, and sorted at the end.
"""

import gzip
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np

from winch_data_processing import formats, idle, units
from winch_data_processing.config import Config

logger = logging.getLogger(__name__)


@dataclass
class WinchData:
    """The cruise's winch records, sorted by time: those `find_casts` needs (all of them with
    `drop_idle=False`), and the tension of every record minute by minute
    """

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
    records: int = 0  # read within the cruise's dates, before idle ones were dropped
    envelope: idle.Envelope | None = None  # every record's tension, minute by minute

    def __len__(self) -> int:
        """The records kept"""
        return len(self.time)

    @property
    def has_payout(self) -> bool:
        return bool(np.isfinite(self.payout).any())


def find_files(directory: Path, pattern: str) -> list[Path]:
    return sorted(p for p in Path(directory).glob(pattern) if p.is_file())


def read_fileset(directory: Path | str, config: Config, drop_idle: bool = True) -> WinchData:
    if drop_idle:
        try:
            return _read(Path(directory), config, drop_idle=True)
        except idle.OutOfOrder:
            logger.info("the files aren't in time order: reading them again, every record kept")
    return _read(Path(directory), config, drop_idle=False)


def _read(directory: Path, config: Config, drop_idle: bool) -> WinchData:
    source = config.data
    reader = formats.READERS[source.format]
    paths = find_files(directory, source.files)
    out = formats.Columns(sum(_lines(path) for path in paths))
    settler = idle.Settler(out, config.thresholds) if drop_idle else None
    files, errors, skipped, names = [], [], {}, Counter()
    records = outside = backwards = 0
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
        logger.info("%s: %d records", path.name, out.size - start)
        time = out.time[start : out.size]
        backwards += int(np.count_nonzero(time[1:] < time[:-1]))
        outside += _keep_cruise(out, start, config)
        _to_units(out, start, source)
        records += out.size - start
        if settler is not None:
            settler.add(start)
    if settler is not None:
        envelope = settler.finish()
        columns = [c[: out.size] for c in (out.time, out.tension, out.payout, out.speed)]
    else:
        columns = [c[: out.size] for c in (out.time, out.tension, out.payout, out.speed)]
        if not _sorted(columns[0]):
            order = np.argsort(columns[0], kind="stable")
            columns = [c[order] for c in columns]
        envelope = idle.envelope(columns[0], columns[1])
    return WinchData(
        *columns, files, errors, skipped, names, outside, backwards, records, envelope
    )


def _keep_cruise(out: formats.Columns, start: int, config: Config) -> int:
    """Drops the file's records, `out` from `start`, outside the cruise's dates; how many"""
    keep = _within(out.time[start : out.size], config)
    dropped = int(np.count_nonzero(~keep))
    if dropped:
        for column in (out.time, out.tension, out.payout, out.speed):
            kept = column[start : out.size][keep]
            column[start : start + len(kept)] = kept
        out.size -= dropped
    return dropped


def _to_units(out: formats.Columns, start: int, source) -> None:
    """The file's tension, payout and speed in the project's units, in place"""
    for name, table in (
        ("tension", units.TENSION),
        ("payout", units.LENGTH),
        ("speed", units.SPEED),
    ):
        scale = units.factor(getattr(source, f"{name}_unit"), table, name)
        if scale != 1.0:
            getattr(out, name)[start : out.size] *= scale


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

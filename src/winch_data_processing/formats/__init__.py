"""The readers of winch data formats.

`READERS` maps a format ID (`[data] format` in the config) to a `Reader`. Each takes a file's
lines and returns its records as columns (`Records`), in the data's units; `rawdata.py` turns
them into the project's units. A reader keeps every record it can parse and counts the lines it
can't, by reason, rather than raising.

Formats so far:
- `lci90i`: the LCI-90i wire monitoring display's network string, as OpenRVDAS's `Winch` device
  type defines it (`contrib/devices/misc_device_types.yaml`).
- `openrvdas_lci90i`: the same, logged by OpenRVDAS, each line led by its ISO 8601 timestamp.
- `delimited`: a delimited text file whose columns the config names (`[data.columns]`).

MOTU, Markey and DynaCon winch control systems' own logs are planned (docs/architecture.md).
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import BinaryIO

import numpy as np

from winch_data_processing.config import DataSource


@dataclass
class Records:
    """A file's records as columns, in the data's units; NaN where a record has no value"""

    time: np.ndarray  # datetime64[ns], UTC
    tension: np.ndarray
    payout: np.ndarray
    speed: np.ndarray
    skipped: Counter = field(default_factory=Counter)  # lines not read, by reason
    names: Counter = field(default_factory=Counter)  # records by the winch name they carry

    def __len__(self) -> int:
        return len(self.time)


class Columns:
    """Columns that records are written into in place, at `size`. `read_fileset` makes them
    for the whole fileset, as long as its lines (no format has more records than lines; a lone
    \r, read as a line end, can, so they grow then), so a file's records go straight in: no
    per-file arrays to join and free. Unwritten capacity is never touched, so never resident
    """

    def __init__(self, capacity: int):
        self.time = np.empty(capacity, "datetime64[ns]")
        self.tension, self.payout, self.speed = (np.empty(capacity) for _ in range(3))
        self.size = 0

    def extend(self, time, tension, payout, speed) -> None:
        end = self.size + len(time)
        if end > len(self.time):
            self._grow(end)
        for column, values in zip(self._columns(), (time, tension, payout, speed), strict=True):
            column[self.size : end] = values
        self.size = end

    def records(self, start: int, skipped: Counter, names: Counter) -> Records:
        """The records written since `start`, as views"""
        return Records(*(c[start : self.size] for c in self._columns()), skipped, names)

    def _columns(self) -> tuple[np.ndarray, ...]:
        return self.time, self.tension, self.payout, self.speed

    def _grow(self, needed: int) -> None:
        capacity = max(needed, len(self.time) * 3 // 2)
        grown = []
        for column in self._columns():
            bigger = np.empty(capacity, column.dtype)
            bigger[: self.size] = column[: self.size]
            grown.append(bigger)
        self.time, self.tension, self.payout, self.speed = grown


Read = Callable[[Iterable[str], DataSource, "str | None"], Records]
# (the file's bytes, data source, record name or None, the columns to write into) -> Records,
# views of what it wrote
ReadFile = Callable[[BinaryIO, DataSource, "str | None", Columns], Records]


@dataclass(frozen=True)
class Reader:
    read: Read  # (lines, data source, record name or None) -> Records
    description: str
    read_file: ReadFile | None = None  # the same from the file's bytes, in place, faster


def records(rows: list[tuple], skipped: Counter, names: Counter) -> Records:
    """Records from (time, tension, payout, speed) rows, times as datetime64[ns]"""
    if not rows:
        empty = np.array([], dtype=float)
        return Records(np.array([], "datetime64[ns]"), empty, empty, empty, skipped, names)
    times, tension, payout, speed = zip(*rows, strict=True)
    return Records(
        np.array(times, dtype="datetime64[ns]"),
        np.array(tension, dtype=float),
        np.array(payout, dtype=float),
        np.array(speed, dtype=float),
        skipped,
        names,
    )


def _readers() -> dict[str, Reader]:
    from winch_data_processing.formats import delimited, lci90i

    return {
        "lci90i": Reader(
            lci90i.read,
            "LCI-90i network string (OpenRVDAS's Winch device type)",
            lci90i.read_file,
        ),
        "openrvdas_lci90i": Reader(
            lci90i.read_logged,
            "LCI-90i strings logged by OpenRVDAS (ISO timestamp first)",
            lci90i.read_logged_file,
        ),
        "delimited": Reader(delimited.read, "delimited text, columns named in [data.columns]"),
    }


READERS = _readers()

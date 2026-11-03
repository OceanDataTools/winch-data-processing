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


Read = Callable[[Iterable[str], DataSource, "str | None"], Records]


@dataclass(frozen=True)
class Reader:
    read: Read  # (lines, data source, record name or None) -> Records
    description: str


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
        "lci90i": Reader(lci90i.read, "LCI-90i network string (OpenRVDAS's Winch device type)"),
        "openrvdas_lci90i": Reader(
            lci90i.read_logged, "LCI-90i strings logged by OpenRVDAS (ISO timestamp first)"
        ),
        "delimited": Reader(delimited.read, "delimited text, columns named in [data.columns]"),
    }


READERS = _readers()

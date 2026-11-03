"""Delimited text: a winch log exported as CSV (or tab-, space-delimited), its columns named in
the config.

`[data.columns]` names the time, tension, payout and speed columns, by header name or by 0-based
index; payout and speed are optional (without payout no deployments can be found, so the checks
on them are n/a). The header is the last of `header_lines` lines. `time_format` is `iso`
(ISO 8601, `Z` optional), `epoch` (seconds since 1970, UTC), or a `strptime` format, read as
UTC.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

import numpy as np

from winch_data_processing.config import ConfigError, DataSource
from winch_data_processing.formats import Records, records
from winch_data_processing.formats.lci90i import parse_time


def read(lines: Iterable[str], source: DataSource, name: str | None) -> Records:
    columns = source.columns
    split = (
        (lambda line: line.split())
        if source.delimiter == " "
        else (lambda line: line.split(source.delimiter))
    )
    lines = iter(lines)
    header = None
    for _ in range(source.header_lines):
        header = next(lines, None)
    names = [h.strip().strip('"') for h in split(header.rstrip("\r\n"))] if header else []
    index = {
        role: _index(getattr(columns, role), names, role)
        for role in ("time", "tension", "payout", "speed")
        if getattr(columns, role) is not None
    }
    parse = _time_parser(source.time_format)
    rows, skipped = [], Counter()
    for line in lines:
        if not line.strip():
            continue
        fields = [f.strip().strip('"') for f in split(line.rstrip("\r\n"))]
        if len(fields) <= max(index.values()):
            skipped["too few fields"] += 1
            continue
        time = parse(fields[index["time"]])
        if time is None:
            skipped["bad time"] += 1
            continue
        try:
            values = [
                float(fields[index[role]]) if role in index else np.nan
                for role in ("tension", "payout", "speed")
            ]
        except ValueError:
            skipped["bad number"] += 1
            continue
        if not np.isfinite(values[0]):
            skipped["bad number"] += 1
            continue
        rows.append((time, *values))
    return records(rows, skipped, Counter())


def _index(column: str | int, names: list[str], role: str) -> int:
    if isinstance(column, int):
        return column
    try:
        return names.index(column)
    except ValueError:
        raise ConfigError(
            f'[data.columns] {role} "{column}" is not in the header ({", ".join(names)})'
        ) from None


def _time_parser(time_format: str):
    if time_format == "iso":
        return parse_time
    if time_format == "epoch":

        def epoch(text: str):
            try:
                return np.datetime64(round(float(text) * 1e9), "ns")
            except ValueError:
                return None

        return epoch

    def strptime(text: str):
        try:
            time = datetime.strptime(text, time_format)
        except ValueError:
            return None
        if time.tzinfo is not None:
            time = time.astimezone(UTC).replace(tzinfo=None)
        return np.datetime64(time, "ns")

    return strptime

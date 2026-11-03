"""The LCI-90i wire monitoring display's data record, raw and as logged by OpenRVDAS.

Measurement Technology NW's LCI-90i answers on its network with records led by a two-character
LAN address and a two-letter message type. The data record is `RD`::

    02RD,2024-04-06T00:25:05.302,-0000046,00000000,000664.5,2843
    LAN ID and RD, display time, tension [lbf], speed [m min-1], payout [m], checksum

The checksum is the sum of the record's bytes up to and including the comma before it, in
decimal (2843 above). Records whose checksum doesn't match are skipped: noisy serial lines
corrupt about one record in 3,000 (RR2403's hydro winch: `0bRD`, `-000003i`). Other message
types (`SU`/`SA` exchanges, `TA`, `DI`, `PA`, ...) aren't data and are counted, not read. The
LAN ID is the record's name: RR2403's hydro display is 02, its trawl display 03.

OpenRVDAS logs each record after its own ISO 8601 timestamp and a space (a comma also
accepted). A record split across two logger lines (`02RD,time,tension,speed` then
`,payout,checksum`, RR2403's trawl winch: about 2,000 a day) is joined and timed by its first
line. `[data] time` picks the clock: the logger's (default) or the display's. RR2403's displays'
clocks were 2 h 21 min (hydro) and a month and 11 hours (trawl) off the logger's, so the
display's clock is only for raw logs without a logger.

OpenRVDAS's `Winch` device type (`contrib/devices/misc_device_types.yaml`) defines the record
with a winch name after the time; a record with that extra field is read too, its name kept,
but its checksum isn't checked: no such record has been seen yet.

Sources: RR2403 (R2R filesets 160178, Markey DESH-5, and 160181, Markey DUTW-9-11; R2R format
100038, "SIO winch HYDRO,TRAWL inpf data").
"""

import re
from collections import Counter
from collections.abc import Iterable

import numpy as np

from winch_data_processing.config import DataSource
from winch_data_processing.formats import Records, records

ISO = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z?"
_LOGGED = re.compile(rf"\s*{ISO}[ ,](.*)")
_TYPE = re.compile(r"[0-9A-Fa-f]{2}([A-Z]{2}),")
FIELDS = 6  # ID and type, time, tension, speed, payout, checksum
NAMED_FIELDS = 7  # OpenRVDAS's definition: a winch name after the time


def read(lines: Iterable[str], source: DataSource, name: str | None) -> Records:
    """The records of an LCI-90i's own log, timed by its clock"""
    rows, skipped, names = [], Counter(), Counter()
    for line in lines:
        if line.strip():
            _record(line.strip(), None, name, rows, skipped, names)
    return records(rows, skipped, names)


def read_logged(lines: Iterable[str], source: DataSource, name: str | None) -> Records:
    """The records of an OpenRVDAS log of an LCI-90i, timed by `source.time`'s clock"""
    rows, skipped, names = [], Counter(), Counter()
    logger = source.time == "logger"
    pending = None  # (stamp, start of a record split across lines)
    for line in lines:
        if not line.strip():
            continue
        match = _LOGGED.match(line)
        if match is None:
            skipped["no logger timestamp"] += 1
            continue
        stamp, record = match.groups()
        record = record.strip()
        if record.startswith(",") and pending is not None:
            stamp, record = pending[0], pending[1] + record
            pending = None
        elif pending is not None:
            skipped["split record without its end"] += 1
            pending = None
        if _TYPE.match(record) and record[2:4] == "RD" and record.count(",") == FIELDS - 3:
            pending = (stamp, record)  # its payout and checksum are on the next line
            continue
        _record(record, stamp if logger else None, name, rows, skipped, names)
    if pending is not None:
        skipped["split record without its end"] += 1
    return records(rows, skipped, names)


def _record(record: str, stamp, name, rows, skipped, names) -> None:
    kind = _TYPE.match(record)
    if kind is None:
        skipped["not an LCI-90i record"] += 1
        return
    if kind.group(1) != "RD":
        skipped[f"{kind.group(1)} message"] += 1
        return
    fields = record.split(",")
    if len(fields) == FIELDS:
        if not checksum_matches(record):
            skipped["bad checksum"] += 1
            return
        record_name, values = fields[0][:2], fields[2:5]
    elif len(fields) == NAMED_FIELDS:
        record_name, values = fields[2].strip(), fields[3:6]
    else:
        skipped["wrong number of fields"] += 1
        return
    names[record_name] += 1
    if name is not None and record_name != name:
        skipped["another winch"] += 1
        return
    time = parse_time(stamp if stamp is not None else fields[1].strip())
    if time is None:
        skipped["bad time"] += 1
        return
    try:
        tension, speed, payout = (float(f) for f in values)
    except ValueError:
        skipped["bad number"] += 1
        return
    if not all(np.isfinite((tension, speed, payout))):
        skipped["bad number"] += 1
        return
    rows.append((time, tension, payout, speed))


def checksum_matches(record: str) -> bool:
    """Whether the record's last field is the sum of its bytes before it, comma included"""
    body, _, checksum = record.rpartition(",")
    return checksum.isdigit() and sum((body + ",").encode("ascii", "replace")) == int(checksum)


def parse_time(text: str) -> np.datetime64 | None:
    """An ISO 8601 UTC time (`Z` optional) as datetime64[ns], or None"""
    text = text.removesuffix("Z")
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?", text):
        return None
    try:
        return np.datetime64(text, "ns")
    except ValueError:
        return None

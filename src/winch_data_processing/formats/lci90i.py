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

Speed and memory: a file is read as bytes, `BLOCK` at a time, its records written into the
fileset's columns (`formats.Columns`). Its usual lines, fixed width (`RECORD`, after `STAMP`
when logged: every RR2403 line but about 1 in 3,000), are checked against the template and
read from their digits with numpy, a block's lines at once; no Python object per line. Every
other line, and one with an impossible time, takes the per-line path (`_Reading.line`), which
decides the same, as does the line after a split record's start, so it's joined. RR2403's
hydro day (1.7 M records): 0.7 s rather than 7 s.

Sources: RR2403 (R2R filesets 160178, Markey DESH-5, and 160181, Markey DUTW-9-11; R2R format
100038, "SIO winch HYDRO,TRAWL inpf data").
"""

import functools
import io
import re
from collections import Counter
from collections.abc import Iterable
from typing import BinaryIO

import numpy as np

from winch_data_processing.config import DataSource
from winch_data_processing.formats import Columns, Records

ISO = r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z?"
_ISO_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?")
_LOGGED = re.compile(rf"\s*{ISO}[ ,](.*)")
_TYPE = re.compile(r"[0-9A-Fa-f]{2}([A-Z]{2}),")
FIELDS = 6  # ID and type, time, tension, speed, payout, checksum
NAMED_FIELDS = 7  # OpenRVDAS's definition: a winch name after the time

BLOCK = 1 << 21  # [bytes] of a file read at a time
# The usual line's characters: d a digit, s a sign or digit, h a hex digit, p a space or comma,
# others themselves. RECORD's fields start at: display time 5, tension 29, speed 38, payout 47,
# checksum 56 (summed: 0 to 55)
RECORD = "hhRD,dddd-dd-ddTdd:dd:dd.ddd,sddddddd,sddddddd,sddddd.d,dddd"
STAMP = "dddd-dd-ddTdd:dd:dd.ddddddZp"  # OpenRVDAS's timestamp, microseconds, and a separator
_SIGNS = np.frombuffer(b"+-", np.uint8)


def read(lines: Iterable[str], source: DataSource, name: str | None) -> Records:
    """The records of an LCI-90i's own log, timed by its clock"""
    data = _as_bytes(lines)
    return read_file(io.BytesIO(data), source, name, Columns(data.count(b"\n")))


def read_logged(lines: Iterable[str], source: DataSource, name: str | None) -> Records:
    """The records of an OpenRVDAS log of an LCI-90i, timed by `source.time`'s clock"""
    data = _as_bytes(lines)
    return read_logged_file(io.BytesIO(data), source, name, Columns(data.count(b"\n")))


def read_file(stream: BinaryIO, source: DataSource, name: str | None, out: Columns) -> Records:
    return _Reading(False, False, name, out).read(stream)


def read_logged_file(
    stream: BinaryIO, source: DataSource, name: str | None, out: Columns
) -> Records:
    return _Reading(True, source.time == "logger", name, out).read(stream)


def _as_bytes(lines: Iterable[str]) -> bytes:
    """Lines as a file's bytes; a character that isn't ASCII becomes ?, which sums as the text
    reading's replacement character did
    """
    text = "".join(line if line.endswith(("\n", "\r")) else f"{line}\n" for line in lines)
    return text.encode("ascii", "replace")


class _Reading:
    """One file's reading: its records, block by block, into `out`, and what it skipped"""

    def __init__(self, logged: bool, logger_clock: bool, name: str | None, out: Columns):
        self.logged, self.logger_clock, self.name = logged, logger_clock, name
        self.out, self.start = out, out.size
        self.template = (STAMP if logged else "") + RECORD
        self.record = len(STAMP) if logged else 0  # where RECORD starts
        self.skipped, self.names = Counter(), Counter()
        self.pending = None  # (stamp, start of a record split across lines)
        self.slow: list[tuple] = []  # (line, time, tension, payout, speed), the block's

    def read(self, stream: BinaryIO) -> Records:
        rest = b""
        while chunk := stream.read(BLOCK):
            data = rest + chunk
            cut = data.rfind(b"\n") + 1
            if cut:
                self._block(data[:cut])
            rest = data[cut:]
        if rest:
            self._block(rest + b"\n")
        if self.pending is not None:
            self.skipped["split record without its end"] += 1
        return self.out.records(self.start, self.skipped, self.names)

    def _block(self, data: bytes) -> None:
        """A block of whole lines, each ending in a newline"""
        buf = np.frombuffer(data, np.uint8)
        ends = np.flatnonzero(buf == ord("\n"))
        starts = np.concatenate(([0], ends[:-1] + 1))
        cr = (ends > starts) & (buf[np.maximum(ends - 1, 0)] == ord("\r"))
        width = len(self.template)
        if not cr.any() and len(ends) * (width + 1) == len(buf) and (ends - starts == width).all():
            rows = np.arange(len(ends))  # every line the template's width: a 2D view
            chars = buf.reshape(-1, width + 1)[:, :width]
        else:
            rows = np.flatnonzero(ends - cr - starts == width)
            chars = buf[starts[rows, None] + np.arange(width)]
        ok = _matches(chars, self.template)
        rows, chars = rows[ok], chars[ok]
        time, ok = self._time(chars)
        rows, chars, time = rows[ok], chars[ok], time[ok]

        fast = np.zeros(len(ends), dtype=bool)
        fast[rows] = True
        self._slow_lines(data, starts, ends, fast)
        keep = fast[rows]  # less the lines after a split record's start
        self._fast_lines(rows[keep], chars[keep], time[keep])

    def _slow_lines(self, data: bytes, starts, ends, fast: np.ndarray) -> None:
        """The lines `fast` doesn't take, one at a time, in order; a fast line after a split
        record's start is taken here instead, so the record's end is looked for in it
        """
        slow = np.flatnonzero(~fast).tolist()
        if self.pending is not None and len(fast) and fast[0]:
            fast[0] = False
            slow.insert(0, 0)
        i = 0
        while i < len(slow):
            line = slow[i]
            i += 1
            # Universal newlines, as text mode reads them: a lone \r ends a line too
            text = data[starts[line] : ends[line]].decode("ascii", "replace")
            for piece in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                self.line(piece, line)
            after = line + 1
            if self.pending is not None and after < len(fast) and fast[after]:
                fast[after] = False
                slow.insert(i, after)

    def line(self, line: str, number: int) -> None:
        """One line, as text: the per-line path"""
        if not line.strip():
            return
        if not self.logged:
            self._record(line.strip(), None, number)
            return
        match = _LOGGED.match(line)
        if match is None:
            self.skipped["no logger timestamp"] += 1
            return
        stamp, record = match.groups()
        record = record.strip()
        if record.startswith(",") and self.pending is not None:
            stamp, record = self.pending[0], self.pending[1] + record
            self.pending = None
        elif self.pending is not None:
            self.skipped["split record without its end"] += 1
            self.pending = None
        if _TYPE.match(record) and record[2:4] == "RD" and record.count(",") == FIELDS - 3:
            self.pending = (stamp, record)  # its payout and checksum are on the next line
            return
        self._record(record, stamp if self.logger_clock else None, number)

    def _record(self, record: str, stamp: str | None, number: int) -> None:
        skipped = self.skipped
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
        self.names[record_name] += 1
        if self.name is not None and record_name != self.name:
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
        self.slow.append((number, time, tension, payout, speed))

    def _time(self, chars: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The template lines' times by the clock read, and which are times (2026-13-01 isn't)"""
        if self.logger_clock:
            return _iso_time(chars[:, : len(STAMP) - 2])
        start = self.record + 5
        return _iso_time(chars[:, start : start + 23])

    def _fast_lines(self, rows: np.ndarray, chars: np.ndarray, time: np.ndarray) -> None:
        """The template lines, as the per-line path would read them, merged with the block's
        per-line records in line order
        """
        record = chars[:, self.record :]
        ok = record[:, :56].sum(axis=1, dtype=np.int64) == _number(record[:, 56:60])
        self._skip("bad checksum", ~ok)
        ids = record[ok, :2].copy().view("S2").ravel()
        found, counts = np.unique(ids, return_counts=True)
        self.names.update(dict(zip((f.decode() for f in found), counts.tolist(), strict=True)))
        if self.name is not None:
            other = ids != self.name.encode("ascii", "replace")
            self._skip("another winch", other)
            ok[ok] = ~other
        record = record[ok]
        columns = [
            rows[ok],
            time[ok],
            _signed(record[:, 29:37]),
            _signed(np.delete(record[:, 47:55], 6, axis=1)) / 10,
            _signed(record[:, 38:46]),
        ]
        if self.slow:
            slow = list(zip(*self.slow, strict=True))
            self.slow = []
            dtypes = (np.intp, "datetime64[ns]", float, float, float)
            columns = [
                np.concatenate((c, np.array(s, dtype=d)))
                for c, s, d in zip(columns, slow, dtypes, strict=True)
            ]
            order = np.argsort(columns[0], kind="stable")
            columns = [c[order] for c in columns]
        self.out.extend(*columns[1:])

    def _skip(self, reason: str, rows: np.ndarray) -> None:
        if count := int(np.count_nonzero(rows)):
            self.skipped[reason] += count


def _matches(chars: np.ndarray, template: str) -> np.ndarray:
    """Which rows of characters fit the template, position by position"""
    positions = _positions(template)
    digit = (chars - np.uint8(ord("0"))) < 10  # uint8 wraps below 0
    ok = digit[:, positions["d"]].all(axis=1)
    literal = positions["literal"]
    ok &= (chars[:, literal] == np.frombuffer(template.encode(), np.uint8)[literal]).all(axis=1)
    sign = chars[:, positions["s"]]
    ok &= (digit[:, positions["s"]] | (sign == ord("-")) | (sign == ord("+"))).all(axis=1)
    letter = (chars[:, positions["h"]] | np.uint8(0x20)) - np.uint8(ord("a")) < 6  # a-f, A-F
    ok &= (digit[:, positions["h"]] | letter).all(axis=1)
    separator = chars[:, positions["p"]]
    ok &= ((separator == ord(" ")) | (separator == ord(","))).all(axis=1)
    return ok


@functools.cache
def _positions(template: str) -> dict[str, np.ndarray]:
    """The template's positions by kind: d, s, h, p, and the literal characters'"""
    kinds = {kind: [] for kind in ("d", "s", "h", "p", "literal")}
    for position, kind in enumerate(template):
        kinds[kind if kind in "dshp" else "literal"].append(position)
    return {kind: np.array(found, dtype=np.intp) for kind, found in kinds.items()}


def _number(digits: np.ndarray) -> np.ndarray:
    """Columns of ASCII digits as integers"""
    values = digits.astype(np.int64) - ord("0")
    return values @ 10 ** np.arange(digits.shape[1] - 1, -1, -1, dtype=np.int64)


def _signed(chars: np.ndarray) -> np.ndarray:
    """A sign or digit, then digits, as floats: as float() reads them, -0000000 is -0.0"""
    first = chars[:, 0]
    sign = np.isin(first, _SIGNS)
    values = _number(np.where(sign[:, None] & (np.arange(chars.shape[1]) == 0), ord("0"), chars))
    return np.where(first == ord("-"), -values.astype(float), values.astype(float))


def _iso_time(chars: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`dddd-dd-ddTdd:dd:dd.d...` as datetime64[ns], and which are real times"""
    year, month, day = _number(chars[:, 0:4]), _number(chars[:, 5:7]), _number(chars[:, 8:10])
    hour, minute = _number(chars[:, 11:13]), _number(chars[:, 14:16])
    second, fraction = _number(chars[:, 17:19]), _number(chars[:, 20:])
    months = ((year - 1970) * 12 + month - 1).astype("datetime64[M]")
    dates = months.astype("datetime64[D]") + (day - 1).astype("timedelta64[D]")
    ok = (month >= 1) & (month <= 12) & (day >= 1) & (dates.astype("datetime64[M]") == months)
    ok &= (hour < 24) & (minute < 60) & (second < 60)
    nanoseconds = ((hour * 60 + minute) * 60 + second) * 10**9
    nanoseconds += fraction * 10 ** (9 - chars.shape[1] + 20)
    return dates.astype("datetime64[ns]") + nanoseconds.astype("timedelta64[ns]"), ok


def checksum_matches(record: str) -> bool:
    """Whether the record's last field is the sum of its bytes before it, comma included"""
    body, _, checksum = record.rpartition(",")
    return checksum.isdigit() and sum((body + ",").encode("ascii", "replace")) == int(checksum)


def parse_time(text: str) -> np.datetime64 | None:
    """An ISO 8601 UTC time (`Z` optional) as datetime64[ns], or None"""
    text = text.removesuffix("Z")
    if not _ISO_TIME.fullmatch(text):
        return None
    try:
        return np.datetime64(text, "ns")
    except ValueError:
        return None

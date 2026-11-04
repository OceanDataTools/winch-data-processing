"""The wire record: a tension member's history log across cruises (A.5.2.1), a directory per
tension member named for its NSF identifier (`record_dir`), holding:

- `deployments.csv`: every deployment of every cruise merged in (`merge`), with the wire log's
  columns and values, and each row's cruise, vessel and winch. Merging a cruise again replaces
  its rows (by cruise and winch), so the OpenVDM hook can merge after every run.
- `tension_member.toml`: the tension member as the latest merged cruise's config gives it, and
  every break test a merged cruise's config has listed (a config's win for its dates), but those
  `events.toml` has.
- `events.toml`: the operator's, edited by hand and never written here: what only their records
  show (A.5.2.1), each kind an array of tables, every entry dated (`EVENT_TABLES`;
  `configs/events.example.toml` shows every key): break tests, cutbacks, spooling,
  lubrication, test results and terminations with their proof tests. Checked when read, as a
  config is.

The record's break tests are `tension_member.toml`'s and `events.toml`'s together; a date with two
loads is an error naming both. With `--wire-record`, a run's checks use them with its config's
(`with_break_tests`), so a break test entered once in `events.toml` sets the ABL and the break
test interval of every later cruise.

Plain files, read and written only here, so a database can replace them if adoption calls for
one. Each the tool writes is written whole to a temporary file and moved into place, so a failed
write leaves the last good record.

"Since the last break test" (A.5.2.1: "number and/or duration of deployments since the last
break test") counts the deployments that started on or after the latest break test's date: a
test has a date, not a time, so a deployment that day is counted rather than missed.
"""

import csv
import io
import os
import tomllib
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

import numpy as np

from winch_data_processing import quantities as q
from winch_data_processing import units
from winch_data_processing.assess import WinchResult
from winch_data_processing.config import (
    BreakTest,
    Config,
    ConfigError,
    TensionMember,
    check_keys,
    dumps_toml,
)
from winch_data_processing.wirelog import COLUMNS as WIRE_LOG_COLUMNS
from winch_data_processing.wirelog import deployment_row, safe_name

DEPLOYMENTS = "deployments.csv"
MEMBER = "tension_member.toml"
EVENTS = "events.toml"
# events.toml's tables, each an array of tables: (label, keys an entry must have, keys it may)
EVENT_TABLES = {
    "break_test": ("Break test", ("date", "load"), ("note",)),
    "cutback": ("Cutback", ("date", "length"), ("reason", "note")),
    "spooling": ("Spooling", ("date", "length"), ("winch", "note")),
    "lubrication": ("Lubrication", ("date", "lubricant"), ("length", "note")),
    "test": ("Test", ("date", "kind", "result"), ("note",)),
    "termination": ("Termination", ("date", "kind"), ("proof_load", "note")),
}
TENSIONS, LENGTHS = ("load", "proof_load"), ("length",)  # converted to lbf and m on reading
EVENTS_SCHEMA = {
    "tension_unit": None,
    "length_unit": None,
    **{kind: dict.fromkeys(need + may) for kind, (_, need, may) in EVENT_TABLES.items()},
}
COLUMNS = (q.CRUISE, q.VESSEL, q.WINCH, *(quantity for quantity, _ in WIRE_LOG_COLUMNS))
NAMES = [quantity.name for quantity in COLUMNS]


class WireRecordError(ValueError):
    """A directory that isn't a wire record, or a record file that can't be read"""


@dataclass(frozen=True)
class Deployment:
    """One row of the record"""

    cruise: str
    vessel: str
    winch: str
    number: int
    start: np.datetime64
    end: np.datetime64
    duration: float  # [s]
    max_payout: float  # [m]
    max_tension: float  # [lbf]
    min_factor_of_safety: float  # ABL / max tension, with that cruise's ABL


@dataclass(frozen=True)
class Event:
    """An entry of events.toml: its table, date, and other values (tensions in lbf, lengths in
    m)
    """

    kind: str
    date: date
    values: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return EVENT_TABLES[self.kind][0]

    def describe(self) -> str:
        v = self.values
        text = {
            "break_test": lambda: f"TBL {q.TENSION.format(v['load'])} lbf",
            "cutback": lambda: (
                f"{_m(v['length'])} cut" + (f" ({v['reason']})" if "reason" in v else "")
            ),
            "spooling": lambda: (
                f"{_m(v['length'])} on the drum" + (f", {v['winch']}" if "winch" in v else "")
            ),
            "lubrication": lambda: (
                v["lubricant"] + (f", {_m(v['length'])}" if "length" in v else "")
            ),
            "test": lambda: f"{v['kind']}: {v['result']}",
            "termination": lambda: (
                v["kind"]
                + (
                    f", proof tested to {q.TENSION.format(v['proof_load'])} lbf"
                    if "proof_load" in v
                    else ""
                )
            ),
        }[self.kind]()
        return f"{text}; {v['note']}" if v.get("note") else text


def _m(metres: float) -> str:
    return f"{metres:,.1f} m"


@dataclass(frozen=True)
class Totals:
    """A set of deployments summed up"""

    deployments: int
    hours: float
    deepest: float  # [m]
    highest_tension: float  # [lbf]
    lowest_factor_of_safety: float
    cruises: int


@dataclass
class WireRecord:
    directory: Path
    tension_member: dict  # the [tension_member] table, as the latest merged cruise's config
    deployments: list[Deployment] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)  # events.toml's, by date

    @property
    def member_id(self) -> str:
        return str(self.tension_member.get("id", self.directory.name))

    @property
    def break_tests(self) -> list[BreakTest]:
        """tension_member.toml's and events.toml's, by date"""
        return joined_break_tests(
            [
                (MEMBER, _member_break_tests(self.tension_member)),
                (EVENTS, _event_break_tests(self.events)),
            ]
        )

    @property
    def last_break_test(self) -> date | None:
        tests = self.break_tests
        return tests[-1].date if tests else None

    def latest(self, kind: str) -> Event | None:
        found = [e for e in self.events if e.kind == kind]
        return found[-1] if found else None

    def length(self) -> tuple[float | None, float]:
        """(the length after the cutbacks since the last spooling, None without one; the
        metres cut back since it, or in all without one)
        """
        spooled = self.latest("spooling")
        since = [
            e
            for e in self.events
            if e.kind == "cutback" and (not spooled or e.date >= spooled.date)
        ]
        cut = sum(e.values["length"] for e in since)
        return (spooled.values["length"] - cut if spooled else None), cut

    def since_last_break_test(self) -> list[Deployment]:
        """The deployments that started on or after the last break test's date (all of them
        without one)
        """
        tested = self.last_break_test
        if tested is None:
            return list(self.deployments)
        day = np.datetime64(tested, "D")
        return [d for d in self.deployments if d.start.astype("datetime64[D]") >= day]


def totals(deployments: list[Deployment]) -> Totals:
    def most(values, default=np.nan, pick=max):
        values = [v for v in values if np.isfinite(v)]
        return pick(values) if values else default

    return Totals(
        deployments=len(deployments),
        hours=sum(d.duration for d in deployments if np.isfinite(d.duration)) / 3600,
        deepest=most(d.max_payout for d in deployments),
        highest_tension=most(d.max_tension for d in deployments),
        lowest_factor_of_safety=most((d.min_factor_of_safety for d in deployments), pick=min),
        cruises=len({(d.cruise, d.winch) for d in deployments}),
    )


def joined_break_tests(groups: list[tuple[str, list[BreakTest]]]) -> list[BreakTest]:
    """Break tests from several places, by date; a date with two loads is an error naming
    where each is
    """
    by_date: dict[date, tuple[BreakTest, str]] = {}
    for where, tests in groups:
        for test in tests:
            seen = by_date.setdefault(test.date, (test, where))
            if abs(seen[0].load - test.load) > max(0.5, 1e-6 * test.load):  # unit conversions
                raise WireRecordError(
                    f"the break test of {test.date} is {q.TENSION.format(seen[0].load)} lbf in "
                    f"{seen[1]} and {q.TENSION.format(test.load)} lbf in {where}: correct one"
                )
    return sorted((test for test, _ in by_date.values()), key=lambda t: t.date)


def with_break_tests(config: Config, records: Path | str) -> Config:
    """The config with its tension member's record's break tests too (none without a record)"""
    member = config.tension_member
    directory = record_dir(records, member.id)
    if not directory.is_dir():
        return config
    record = load(directory)
    tests = joined_break_tests(
        [("the config", list(member.break_tests)), ("the record", record.break_tests)]
    )
    return replace(config, tension_member=replace(member, break_tests=tuple(tests)))


def _member_break_tests(table: dict) -> list[BreakTest]:
    return [BreakTest(t["date"], float(t["load"])) for t in table.get("break_test", [])]


def _event_break_tests(events: list[Event]) -> list[BreakTest]:
    return [BreakTest(e.date, e.values["load"]) for e in events if e.kind == "break_test"]


def record_dir(records: Path | str, member_id: str) -> Path:
    """A tension member's record under the records directory, named for its NSF identifier"""
    return Path(records) / safe_name(member_id)


def merge(records: Path | str, result: WinchResult) -> Path:
    """The run's deployments into its tension member's record, replacing any of the same
    cruise and winch; the record's directory
    """
    config, member = result.config, result.config.tension_member
    directory = record_dir(records, member.id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / DEPLOYMENTS
    rows = _read_rows(path) if path.exists() else []
    this = (config.cruise_id, config.winch_name)
    rows = [row for row in rows if (row["cruise"], row["winch"]) != this]
    abl = member.assigned_breaking_load
    for cast in result.casts:
        values = [config.cruise_id, config.vessel_name, config.winch_name]
        rows.append(dict(zip(NAMES, values + deployment_row(cast, abl), strict=True)))
    rows.sort(key=lambda row: row["start"])  # fixed-width ISO times sort as times
    _write(path, _deployments_text(member.id, rows), Path(records))
    # Every break test a config has listed (this one's win for its dates), but events.toml's
    tests = {}
    if (directory / MEMBER).exists():
        tests = {t.date: t for t in _member_break_tests(_read_member(directory / MEMBER))}
    tests |= {t.date: t for t in member.break_tests}
    events = set(_event_break_tests(load_events(directory / EVENTS)))
    kept = sorted((t for t in tests.values() if t not in events), key=lambda t: t.date)
    _write(directory / MEMBER, member_toml(member, kept), Path(records))
    return directory


def member_toml(member: TensionMember, break_tests: list[BreakTest]) -> str:
    """The tension member as a config's [tension_member] table, in lbf and inches, with the
    break tests the configs have listed
    """
    table = {
        "id": member.id,
        "kind": member.kind.value,
        "description": member.description,
        "nominal_breaking_load": member.nominal_breaking_load,
        "factor_of_safety": member.factor_of_safety,
        "diameter": member.diameter,
        "largest_wire_diameter": member.largest_wire_diameter,
        "break_test": [{"date": t.date, "load": t.load} for t in break_tests],
    }
    table = {k: v for k, v in table.items() if v not in (None, "", [])}
    header = (
        "# The tension member as the latest cruise merged into this record gives it, and the\n"
        "# break tests merged cruises' configs have listed (events.toml's are there)\n"
    )
    return header + dumps_toml({"tension_member": table})


def load(directory: Path | str) -> WireRecord:
    """A tension member's record: its deployments, or only its events before its first cruise"""
    directory = Path(directory)
    path, events = directory / DEPLOYMENTS, directory / EVENTS
    if not path.is_file() and not events.is_file():
        raise WireRecordError(
            f"{directory} is not a wire record: it has no {DEPLOYMENTS} or {EVENTS}"
        )
    member = _read_member(directory / MEMBER) if (directory / MEMBER).exists() else {}
    deployments = [_deployment(row, path) for row in _read_rows(path)] if path.is_file() else []
    return WireRecord(directory, member, deployments, load_events(events))


def _read_member(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text()).get("tension_member", {})
    except tomllib.TOMLDecodeError as error:
        raise WireRecordError(f"{path}: {error}") from None


def load_events(path: Path) -> list[Event]:
    """events.toml's entries, by date (none without the file), checked as a config is"""
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text())
        check_keys(data, EVENTS_SCHEMA, str(path), arrays={(kind,) for kind in EVENT_TABLES})
        tension = units.factor(data.get("tension_unit", "lbf"), units.TENSION, "tension")
        length = units.factor(data.get("length_unit", "m"), units.LENGTH, "length")
    except (tomllib.TOMLDecodeError, ConfigError, units.UnitError) as error:
        message = str(error) if str(error).startswith(str(path)) else f"{path}: {error}"
        raise WireRecordError(message) from None
    events = []
    for kind, (label, need, _) in EVENT_TABLES.items():
        for number, entry in enumerate(data.get(kind, []), 1):
            where = f"{path}: [[{kind}]] {number}"
            missing = [key for key in need if key not in entry]
            if missing:
                raise WireRecordError(f"{where} has no {', '.join(missing)}")
            if not isinstance(entry["date"], date) or isinstance(entry["date"], datetime):
                raise WireRecordError(f"{where}: date must be a date, e.g. 2026-06-19")
            values = {}
            for key, value in entry.items():
                if key == "date":
                    continue
                if key in TENSIONS or key in LENGTHS:
                    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
                        raise WireRecordError(f"{where}: {key} must be a positive number")
                    value = float(value) * (tension if key in TENSIONS else length)
                elif not isinstance(value, str):
                    raise WireRecordError(f"{where}: {key} must be text")
                values[key] = value
            events.append(Event(kind, entry["date"], values))
    return sorted(events, key=lambda e: e.date)


def _deployment(row: dict, path: Path) -> Deployment:
    def number(name: str) -> float:
        text = row[name]
        return np.nan if text == q.MISSING else float(text)

    def time(name: str) -> np.datetime64:
        return np.datetime64(row[name].removesuffix("Z"), "ns")

    try:
        return Deployment(
            cruise=row["cruise"],
            vessel=row["vessel"],
            winch=row["winch"],
            number=int(row["cast"]),
            start=time("start"),
            end=time("end"),
            duration=number("duration"),
            max_payout=number("max_payout"),
            max_tension=number("max_tension"),
            min_factor_of_safety=number("min_factor_of_safety"),
        )
    except ValueError as error:
        raise WireRecordError(
            f"{path}: cruise {row['cruise']} cast {row['cast']}: {error}"
        ) from None


def _read_rows(path: Path) -> list[dict]:
    lines = [line for line in path.read_text().splitlines() if not line.startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames != NAMES:
        raise WireRecordError(
            f"{path}: not a wire record's deployments: its columns are {reader.fieldnames}"
        )
    return list(reader)


def _deployments_text(member_id: str, rows: list[dict]) -> str:
    out = io.StringIO()
    out.write(f"# tension_member: {member_id}\n")
    out.write(f"# field_unit: {','.join(quantity.unit or '' for quantity in COLUMNS)}\n")
    out.write(f"# field_long_name: {','.join(quantity.description for quantity in COLUMNS)}\n")
    writer = csv.DictWriter(out, NAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _write(path: Path, text: str, records: Path) -> None:
    """The file whole, through a temporary file; run as root (the OpenVDM hook), it's given to
    the records directory's owner
    """
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        owner = records.stat()
        for owned in (path, path.parent):
            os.chown(owned, owner.st_uid, owner.st_gid)

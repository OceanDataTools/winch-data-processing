"""The wire record: a tension member's history log across cruises (A.5.2.1), a directory per
tension member named for its NSF identifier (`record_dir`), holding:

- `deployments.csv`: every deployment of every cruise merged in (`merge`), with the wire log's
  columns and values, and each row's cruise, vessel and winch. Merging a cruise again replaces
  its rows (by cruise and winch), so the OpenVDM hook can merge after every run.
- `tension_member.toml`: the tension member as the latest merged cruise's config gives it, its
  break tests included (until the record keeps them itself: Phase 4's step 3).

Plain files, read and written only here, so a database can replace them if adoption calls for
one. Each is written whole to a temporary file and moved into place, so a failed write leaves
the last good record.

"Since the last break test" (A.5.2.1: "number and/or duration of deployments since the last
break test") counts the deployments that started on or after the latest break test's date: a
test has a date, not a time, so a deployment that day is counted rather than missed.
"""

import csv
import io
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from winch_data_processing import quantities as q
from winch_data_processing.assess import WinchResult
from winch_data_processing.config import TensionMember, dumps_toml
from winch_data_processing.wirelog import COLUMNS as WIRE_LOG_COLUMNS
from winch_data_processing.wirelog import deployment_row, safe_name

DEPLOYMENTS = "deployments.csv"
MEMBER = "tension_member.toml"
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

    @property
    def member_id(self) -> str:
        return str(self.tension_member.get("id", self.directory.name))

    @property
    def last_break_test(self) -> date | None:
        tests = self.tension_member.get("break_test", [])
        return max((t["date"] for t in tests), default=None)

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
    _write(directory / MEMBER, member_toml(member), Path(records))
    return directory


def member_toml(member: TensionMember) -> str:
    """The tension member as a config's [tension_member] table, in lbf and inches"""
    table = {
        "id": member.id,
        "kind": member.kind.value,
        "description": member.description,
        "nominal_breaking_load": member.nominal_breaking_load,
        "factor_of_safety": member.factor_of_safety,
        "diameter": member.diameter,
        "largest_wire_diameter": member.largest_wire_diameter,
        "break_test": [{"date": t.date, "load": t.load} for t in member.break_tests],
    }
    table = {k: v for k, v in table.items() if v not in (None, "", [])}
    header = "# The tension member as the latest cruise merged into this record gives it\n"
    return header + dumps_toml({"tension_member": table})


def load(directory: Path | str) -> WireRecord:
    """A tension member's record"""
    directory = Path(directory)
    path = directory / DEPLOYMENTS
    if not path.is_file():
        raise WireRecordError(f"{directory} is not a wire record: it has no {DEPLOYMENTS}")
    try:
        member = tomllib.loads((directory / MEMBER).read_text()).get("tension_member", {})
    except FileNotFoundError:
        member = {}
    except tomllib.TOMLDecodeError as error:
        raise WireRecordError(f"{directory / MEMBER}: {error}") from None
    return WireRecord(directory, member, [_deployment(row, path) for row in _read_rows(path)])


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

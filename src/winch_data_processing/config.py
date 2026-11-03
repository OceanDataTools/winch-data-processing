"""The config TOML: the winch, its tension member, its sheave train and tension monitoring, and
how its data is read. `configs/config.example.toml` shows every key.

Breaking loads and tensions are in the config's `tension_unit` (default lbf), diameters in
`diameter_unit` (default inches); both are converted to the project's units (`units.py`) as
the config is read. Unknown keys are an error that says where they're allowed.
"""

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from winch_data_processing import rvss, units
from winch_data_processing.rvss import Kind


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BreakTest:
    """A sample's Tested Breaking Load (A.2.21) [lbf] and the date of the test"""

    date: date
    load: float


@dataclass(frozen=True)
class TensionMember:
    """The rope or cable on the winch (A.2.20)"""

    id: str  # the NSF tension member identifier (A.5.2.1)
    kind: Kind
    nominal_breaking_load: float  # NBL [lbf], for the end condition in use (A.2.11, A.2.12)
    factor_of_safety: float  # the FS the operator selected (A.5.1.1)
    description: str = ""
    diameter: float | None = None  # d [in]
    largest_wire_diameter: float | None = None  # d1 [in]
    break_tests: tuple[BreakTest, ...] = ()  # oldest first

    @property
    def assigned_breaking_load(self) -> float:
        """ABL [lbf]: the latest TBL when it's below the NBL, else the NBL (A.2.1, A.5.1.1)"""
        if not self.break_tests:
            return self.nominal_breaking_load
        return min(self.nominal_breaking_load, self.break_tests[-1].load)

    @property
    def safe_working_tension(self) -> float:
        """SWT = ABL / FS [lbf] (A.2.19)"""
        return self.assigned_breaking_load / self.factor_of_safety

    @property
    def abl_decreased(self) -> bool:
        """Whether the latest break test found the ABL 10 % or more below the one before it
        (the NBL, or the previous test's ABL), which shortens the testing interval
        """
        if not self.break_tests:
            return False
        before = self.nominal_breaking_load
        if len(self.break_tests) > 1:
            before = min(before, self.break_tests[-2].load)
        return self.assigned_breaking_load <= before * (1 - rvss.ABL_DECREASE)

    @property
    def table(self) -> rvss.Table:
        return rvss.table_for(self.kind, self.factor_of_safety)


@dataclass(frozen=True)
class Sheave:
    """A sheave or load-carrying roller between the winch and the water (A.8.2)"""

    name: str
    tread_diameter: float  # D, the root diameter [in]
    groove_width: float | None = None  # w [in]


@dataclass(frozen=True)
class Monitoring:
    """The tension measuring system (Tables A.8.2 to A.8.5)"""

    system: str = ""
    display_rate: float | None = None  # [Hz]
    last_calibration: date | None = None
    accuracy: float | None = None  # [% of the applied load]
    alarm_tension: float | None = None  # [lbf]


@dataclass(frozen=True)
class Columns:
    """A delimited file's columns, by header name or 0-based index"""

    time: str | int
    tension: str | int
    payout: str | int | None = None
    speed: str | int | None = None


@dataclass(frozen=True)
class DataSource:
    """How the winch's raw files are found and read"""

    format: str
    files: str = "*"  # glob under the data directory, recursive with **
    time: str = "logger"  # logger or instrument: whose clock, where a record has both
    tension_unit: str = "lbf"
    payout_unit: str = "m"
    speed_unit: str = "m min-1"
    columns: Columns | None = None  # delimited only
    delimiter: str = ","
    header_lines: int = 1
    time_format: str = "iso"  # iso, or a strptime format


@dataclass(frozen=True)
class Thresholds:
    """How deployments are found (casts.py) and what the data checks allow"""

    deploy_payout: float = 10.0  # [m] payout beyond which the tension member is deployed
    merge_gap: float = 120.0  # [s] dips below deploy_payout shorter than this are one cast
    min_cast_duration: float = 60.0  # [s]
    cast_padding: float = 300.0  # [s] before and after, for the lift and the recovery
    max_wire_speed: float = 300.0  # [m min-1] faster payout steps are counter resets
    max_logging_gap: float = 5.0  # [s] longest gap in a cast's tension record


@dataclass(frozen=True)
class Config:
    cruise_id: str
    winch_name: str
    tension_member: TensionMember
    data: DataSource
    winch_manufacturer: str = ""
    winch_model: str = ""
    record_name: str | None = None  # the winch's name in its records, to select them
    cruise_start: date | None = None
    cruise_end: date | None = None
    sheaves: tuple[Sheave, ...] = ()
    monitoring: Monitoring = field(default_factory=Monitoring)
    thresholds: Thresholds = field(default_factory=Thresholds)
    path: Path | None = None


SCHEMA = {
    "cruise": {"id": None, "start": None, "end": None},
    "winch": {"name": None, "manufacturer": None, "model": None, "record_name": None},
    "tension_member": {
        "id": None,
        "kind": None,
        "description": None,
        "nominal_breaking_load": None,
        "factor_of_safety": None,
        "diameter": None,
        "largest_wire_diameter": None,
        "tension_unit": None,
        "diameter_unit": None,
        "break_test": {"date": None, "load": None},
    },
    "sheave": {"name": None, "tread_diameter": None, "groove_width": None},
    "monitoring": {
        "system": None,
        "display_rate": None,
        "last_calibration": None,
        "accuracy": None,
        "alarm_tension": None,
    },
    "data": {
        "format": None,
        "files": None,
        "time": None,
        "tension_unit": None,
        "payout_unit": None,
        "speed_unit": None,
        "delimiter": None,
        "header_lines": None,
        "time_format": None,
        "columns": {"time": None, "tension": None, "payout": None, "speed": None},
    },
    "thresholds": {name: None for name in Thresholds.__dataclass_fields__},
}
# Which tables are arrays of tables ([[sheave]], [[tension_member.break_test]])
ARRAYS = {("sheave",), ("tension_member", "break_test")}


def load_config(path: Path | str) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None
    return config_from_dict(data, path, str(path))


def loads_config(text: str) -> Config:
    return config_from_dict(tomllib.loads(text), None, "the config")


def config_from_dict(data: dict, path: Path | None, where: str) -> Config:
    check_keys(data, SCHEMA, where)
    try:
        return _config(data, path)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ConfigError | rvss.StandardError):
            raise
        message = f"no {error.args[0]}" if isinstance(error, KeyError) else str(error)
        raise ConfigError(f"{where}: {message}") from None


def _config(data: dict, path: Path | None) -> Config:
    cruise, winch = data.get("cruise", {}), data.get("winch", {})
    member = data.get("tension_member")
    if member is None:
        raise ConfigError("no [tension_member]")
    if "data" not in data:
        raise ConfigError("no [data]")
    tension_unit = member.get("tension_unit", "lbf")
    diameter_unit = member.get("diameter_unit", "in")

    def tension(value):
        return (
            None
            if value is None
            else units.convert(float(value), tension_unit, units.TENSION, "tension")
        )

    def diameter(value):
        return (
            None
            if value is None
            else units.convert(float(value), diameter_unit, units.DIAMETER, "diameter")
        )

    tests = sorted(
        (
            BreakTest(_date(t["date"], "tension_member.break_test.date"), tension(t["load"]))
            for t in member.get("break_test", [])
        ),
        key=lambda test: test.date,
    )
    tension_member = TensionMember(
        id=str(_required(member, "id", "tension_member")),
        kind=_kind(_required(member, "kind", "tension_member")),
        nominal_breaking_load=tension(
            _required(member, "nominal_breaking_load", "tension_member")
        ),
        factor_of_safety=float(_required(member, "factor_of_safety", "tension_member")),
        description=member.get("description", ""),
        diameter=diameter(member.get("diameter")),
        largest_wire_diameter=diameter(member.get("largest_wire_diameter")),
        break_tests=tuple(tests),
    )
    rvss.table_for(tension_member.kind, tension_member.factor_of_safety)  # refuses a bad FS
    sheaves = tuple(
        Sheave(
            name=str(_required(s, "name", "sheave")),
            tread_diameter=diameter(_required(s, "tread_diameter", "sheave")),
            groove_width=diameter(s.get("groove_width")),
        )
        for s in data.get("sheave", [])
    )
    m = data.get("monitoring", {})
    monitoring = Monitoring(
        system=m.get("system", ""),
        display_rate=_float(m.get("display_rate")),
        last_calibration=_date(m["last_calibration"], "monitoring.last_calibration")
        if "last_calibration" in m
        else None,
        accuracy=_float(m.get("accuracy")),
        alarm_tension=tension(m.get("alarm_tension")),
    )
    return Config(
        cruise_id=str(_required(cruise, "id", "cruise")),
        winch_name=str(_required(winch, "name", "winch")),
        winch_manufacturer=winch.get("manufacturer", ""),
        winch_model=winch.get("model", ""),
        record_name=winch.get("record_name"),
        cruise_start=_date(cruise["start"], "cruise.start") if "start" in cruise else None,
        cruise_end=_date(cruise["end"], "cruise.end") if "end" in cruise else None,
        tension_member=tension_member,
        sheaves=sheaves,
        monitoring=monitoring,
        data=_data_source(data["data"]),
        thresholds=Thresholds(**{k: float(v) for k, v in data.get("thresholds", {}).items()}),
        path=path,
    )


def _data_source(table: dict) -> DataSource:
    from winch_data_processing import formats  # formats imports this module's dataclasses

    format_id = str(_required(table, "format", "data"))
    if format_id not in formats.READERS:
        known = ", ".join(formats.READERS)
        raise ConfigError(f'[data] format "{format_id}" is not one of {known}')
    columns = table.get("columns")
    if format_id == "delimited" and columns is None:
        raise ConfigError('[data] format "delimited" needs [data.columns] time and tension')
    for unit, known, what in (
        ("tension_unit", units.TENSION, "data tension"),
        ("payout_unit", units.LENGTH, "data payout"),
        ("speed_unit", units.SPEED, "data speed"),
    ):
        if unit in table:
            units.factor(table[unit], known, what)
    time = table.get("time", "logger")
    if time not in ("logger", "instrument"):
        raise ConfigError(f'[data] time "{time}" is not "logger" or "instrument"')
    return DataSource(
        format=format_id,
        files=table.get("files", "*"),
        time=time,
        tension_unit=table.get("tension_unit", "lbf"),
        payout_unit=table.get("payout_unit", "m"),
        speed_unit=table.get("speed_unit", "m min-1"),
        columns=None
        if columns is None
        else Columns(
            time=_required(columns, "time", "data.columns"),
            tension=_required(columns, "tension", "data.columns"),
            payout=columns.get("payout"),
            speed=columns.get("speed"),
        ),
        delimiter=table.get("delimiter", ","),
        header_lines=int(table.get("header_lines", 1)),
        time_format=table.get("time_format", "iso"),
    )


def _required(table: dict, key: str, where: str):
    if key not in table:
        raise ConfigError(f"[{where}] needs {key}")
    return table[key]


def _kind(value) -> Kind:
    try:
        return Kind(value)
    except ValueError:
        known = ", ".join(f'"{k}"' for k in Kind)
        raise ConfigError(f'[tension_member] kind "{value}" is not one of {known}') from None


def _float(value) -> float | None:
    return None if value is None else float(value)


def _date(value, where: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ConfigError(f"{where}: {value!r} is not a date (YYYY-MM-DD)") from None


def check_keys(data: dict, schema: dict, where: str, path: tuple[str, ...] = ()) -> None:
    """Unknown keys, and tables where values belong or values where tables do, are errors"""
    for key, value in data.items():
        here = (*path, key)
        name = ".".join(here)
        if key not in schema:
            allowed = ", ".join(schema) if schema else "nothing"
            table = f"[{'.'.join(path)}]" if path else "the top level"
            raise ConfigError(f"{where}: unknown key {name!r}; {table} takes {allowed}")
        sub = schema[key]
        if sub is None:
            if isinstance(value, dict) or (
                isinstance(value, list) and value and isinstance(value[0], dict)
            ):
                raise ConfigError(f"{where}: {name} is a value, not a table")
            continue
        tables = value if here in ARRAYS and isinstance(value, list) else [value]
        if here in ARRAYS and not isinstance(value, list):
            raise ConfigError(f"{where}: {name} is an array of tables ([[{name}]])")
        for table in tables:
            if not isinstance(table, dict):
                raise ConfigError(f"{where}: {name} is a table, not a value")
            check_keys(table, sub, where, here)

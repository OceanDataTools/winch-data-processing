"""The checks of a cruise's winch data and config against RVSS Appendix A (`rvss.py`).

Each `Check` names the requirement it tests and its section, and is pass, fail, warn, info or
n/a. Fail means the data or config shows the requirement wasn't met; warn, that it can't be
shown met (something's missing from the config), or that the data shows what the operator
should look into (tension past the alarm point); info, what only the operator's records can
show. The table the selected FS falls in (A.8.1 to A.8.5) sets which checks apply.

Decisions (inferred; Appendix A doesn't say how to measure them):
- A cast's logging rate is its median sample interval's; it meets a required rate within 2 %,
  for timestamps rounded to the millisecond (20 Hz logged as 0.050 s and 0.051 s).
- Calibration and break test intervals are counted back from the last record's date (or the
  cruise's end, or today, without records), whole calendar months.
- Slow-haul checks (Table A.8.4: "halting a deployment at regular intervals (~ 500 m) and
  conducting a slow haul" once FS 2.0 is reached) apply when the selected FS is in Table A.8.4.
  In each deployment they start at the first record of its deployed stretch, paying out, where
  tension reaches FS 2.0; from there to the deepest payout, a halt is the payout reached so far
  not growing by a whole metre for `slow_haul_halt` (30 s): the winch stopped, or hauling in,
  so a slow haul counts. Between where FS 2.0 was reached, each halt and the deepest payout,
  the wire goes at most 500 m and `slow_haul_allowance` (10 %, for the "~"). Whether the slow
  haul established the nominal and peak tensions is the operator's to show, not the data's; a
  logging gap that long looks like a halt (Gaps in the tension record flags it).
- Tension past the alarm point warns: the tables require an alarm before their FS
  (`rvss.Table.alarm_fs`), not that tension stay below it, so a crossing is for the operator to
  look into (did the alarm sound?), not a fail. The point is the lower of
  `[monitoring] alarm_tension` and ABL / the table's alarm FS, so an alarm set too high (Tension
  alarm setting fails) doesn't hide the table's. In each deployment, a spell above starts at its
  first sample above and ends at the next sample at or below it (or the deployment's last
  record): the time above is those spells' total.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from itertools import pairwise

import numpy as np

from winch_data_processing import rvss
from winch_data_processing.casts import Cast
from winch_data_processing.config import Config
from winch_data_processing.quantities import (
    FACTOR_OF_SAFETY,
    LOGGING_RATE,
    TENSION,
    format_time,
)
from winch_data_processing.rawdata import WinchData

RATE_TOLERANCE = 0.02


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    INFO = "info"
    NA = "n/a"


@dataclass(frozen=True)
class Check:
    name: str
    section: str  # Appendix A's section or table
    status: Status
    detail: str


def run_checks(config: Config, data: WinchData, casts: list[Cast]) -> list[Check]:
    member = config.tension_member
    table = member.table
    on = reference_date(config, data)
    return [
        _casts_found(data, casts),
        _within_swt(config, data, casts),
        _kind_minimum(config, data, casts),
        _elastic_limit(config, data, casts),
        _slow_haul(config, data, casts),
        _logging_rate(table, casts),
        _logging_gaps(config, casts),
        _alarm_setting(config, table),
        _alarm_crossings(config, data, casts),
        _alarms_logged(table),
        _display_rate(config, table),
        _calibration(config, table, on),
        _accuracy(config, table),
        _sheaves(config, table),
        _break_test(config, table, on),
        _abl_retirement(config),
    ]


def reference_date(config: Config, data: WinchData) -> date:
    if len(data):
        return data.time[-1].astype("datetime64[D]").item()
    return config.cruise_end or date.today()


def min_fs(config: Config, tension: float) -> float:
    abl = config.tension_member.assigned_breaking_load
    return abl / tension if tension > 0 else np.inf


def _casts_found(data: WinchData, casts: list[Cast]) -> Check:
    name, section = "Deployments", "A.5.2.1"
    if not len(data):
        return Check(name, section, Status.FAIL, "no records")
    if not data.has_payout:
        return Check(name, section, Status.WARN, "no payout in the data: no deployments found")
    if not casts:
        return Check(name, section, Status.INFO, "none: the payout never passed deploy_payout")
    return Check(name, section, Status.INFO, f"{len(casts)} found")


def _over(config, data, casts, limit_fs: float) -> list[tuple[Cast, int, float]]:
    """Each cast with tension beyond ABL / limit_fs: (cast, samples beyond, its min FS)"""
    limit = config.tension_member.assigned_breaking_load / limit_fs
    found = []
    for cast in casts:
        beyond = int(np.count_nonzero(data.tension[cast.start : cast.stop] > limit))
        if beyond:
            found.append((cast, beyond, min_fs(config, cast.max_tension)))
    return found


def _listed(found) -> str:
    return "; ".join(
        f"cast {c.number}: {n} samples, FS {FACTOR_OF_SAFETY.format(fs)}" for c, n, fs in found
    )


def _within_swt(config, data, casts) -> Check:
    member = config.tension_member
    name = "Tension within the safe working tension"
    section = f"{member.table.name}, A.2.19"
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    swt = f"SWT {TENSION.format(member.safe_working_tension)} lbf (FS {member.factor_of_safety:g})"
    found = _over(config, data, casts, member.factor_of_safety)
    if not found:
        return Check(name, section, Status.PASS, f"every deployment within the {swt}")
    return Check(name, section, Status.FAIL, f"beyond the {swt}: {_listed(found)}")


def _kind_minimum(config, data, casts) -> Check:
    kind = config.tension_member.kind
    lowest = rvss.MIN_FS[kind]
    name = "Never below the lowest FS allowed"
    section = "A.8.3.1" if kind is rvss.Kind.SYNTHETIC else "Tables A.8.3, A.8.4"
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    found = _over(config, data, casts, lowest)
    what = f"FS {lowest:g} for a {kind.replace('_', ' ')}"
    if not found:
        return Check(name, section, Status.PASS, f"never below {what}")
    return Check(name, section, Status.FAIL, f"below {what}, which is forbidden: {_listed(found)}")


def _elastic_limit(config, data, casts) -> Check:
    kind = config.tension_member.kind
    name, section = "Peak tension within the elastic limit", "A.5.6"
    if kind not in rvss.ELASTIC_LIMIT_FS:
        return Check(name, section, Status.NA, "steel tension members only")
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    limit = rvss.ELASTIC_LIMIT_FS[kind]
    found = _over(config, data, casts, limit)
    if not found:
        return Check(name, section, Status.PASS, f"never beyond FS {limit:g}")
    return Check(
        name,
        section,
        Status.FAIL,
        f"beyond FS {limit:g}: the length must be cut back or retired: {_listed(found)}",
    )


@dataclass(frozen=True)
class SlowHauls:
    """A deployment's paying out from where tension reached FS 2.0: the payout there, at each
    halt, and the deepest [m]
    """

    reached: float
    halts: tuple[float, ...]
    deepest: float

    @property
    def stretches(self) -> list[tuple[float, float]]:
        """(from, to) [m] of each stretch paid out without a halt"""
        points = (self.reached, *self.halts, self.deepest)
        return list(pairwise(points))

    @property
    def longest(self) -> tuple[float, float]:
        return max(self.stretches, key=lambda s: s[1] - s[0])


def slow_hauls(data: WinchData, cast: Cast, limit: float, halt: float) -> SlowHauls | None:
    """The cast's halts once tension reached `limit` [lbf] paying out, a halt being `halt` [s]
    or longer before the payout reached so far grows another whole metre; None if it didn't
    reach `limit` before its deepest payout
    """
    records = slice(cast.deployed_start, cast.deployed_stop)
    payout, tension = data.payout[records], data.tension[records]
    if not len(payout) or np.isnan(payout).all():
        return None
    deepest = int(np.nanargmax(payout))
    over = np.flatnonzero(tension[: deepest + 1] >= limit)
    if not len(over):
        return None
    first = int(over[0])
    times = data.time[records][first : deepest + 1]
    reached = np.fmax.accumulate(payout[first : deepest + 1])
    metres = np.floor(reached)
    new = np.flatnonzero(np.concatenate(([True], metres[1:] != metres[:-1])))
    waits = np.diff(times[new]) / np.timedelta64(1, "s")
    halts = reached[new[:-1][waits >= halt]]
    return SlowHauls(float(reached[0]), tuple(float(h) for h in halts), float(reached[-1]))


def _metres(value: float) -> str:
    return f"{value:,.0f} m"


def _slow_haul(config: Config, data: WinchData, casts) -> Check:
    member, thresholds = config.tension_member, config.thresholds
    name, section = "Slow-haul checks below FS 2.0", "Table A.8.4"
    if member.table is not rvss.A_8_4:
        return Check(
            name,
            section,
            Status.NA,
            f"Table A.8.4 only (wire rope from FS {rvss.SLOW_HAUL_FS:g} to 1.5); the selected FS "
            f"{member.factor_of_safety:g} is in {member.table.name}",
        )
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    if not data.has_payout:
        return Check(name, section, Status.WARN, "no payout in the data: halts can't be found")
    limit = member.assigned_breaking_load / rvss.SLOW_HAUL_FS
    found = [
        (cast, hauls)
        for cast in casts
        if (hauls := slow_hauls(data, cast, limit, thresholds.slow_haul_halt)) is not None
    ]
    if not found:
        return Check(
            name, section, Status.NA, f"no deployment reached FS {rvss.SLOW_HAUL_FS:g} paying out"
        )
    allowed = rvss.SLOW_HAUL_INTERVAL * (1 + thresholds.slow_haul_allowance)
    long = [(c, h.longest) for c, h in found if h.longest[1] - h.longest[0] > allowed]
    if long:
        listed = "; ".join(
            f"cast {c.number}: {_metres(end - start)} from {_metres(start)} without a halt"
            for c, (start, end) in long
        )
        return Check(
            name,
            section,
            Status.FAIL,
            f"{listed} (a halt and slow haul every ~500 m, at most {_metres(allowed)})",
        )
    listed = "; ".join(
        f"cast {c.number}: from {_metres(h.reached)}, {len(h.halts)} halts, longest "
        f"{_metres(h.longest[1] - h.longest[0])}"
        for c, h in found
    )
    return Check(
        name,
        section,
        Status.PASS,
        f"a halt or slow haul every {_metres(allowed)} or less past FS {rvss.SLOW_HAUL_FS:g}: "
        f"{listed}; the slow hauls' tensions are the operator's to verify",
    )


def _logging_rate(table: rvss.Table, casts) -> Check:
    name, section = "Tension logging rate", table.name
    if table.logging_hz is None:
        return Check(name, section, Status.NA, f"{table.name} requires no tension logging")
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    required = table.logging_hz
    slow = [c for c in casts if not c.logging_rate >= required * (1 - RATE_TOLERANCE)]
    if not slow:
        return Check(
            name, section, Status.PASS, f"every deployment logged at {required:g} Hz or more"
        )
    listed = "; ".join(f"cast {c.number}: {LOGGING_RATE.format(c.logging_rate)} Hz" for c in slow)
    return Check(name, section, Status.FAIL, f"below {required:g} Hz: {listed}")


def _logging_gaps(config: Config, casts) -> Check:
    name, section = "Gaps in the tension record", "Tables A.8.2 to A.8.5"
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    limit = config.thresholds.max_logging_gap
    gappy = [c for c in casts if c.longest_gap > limit]
    if not gappy:
        return Check(name, section, Status.PASS, f"no gap over {limit:g} s in a deployment")
    listed = "; ".join(f"cast {c.number}: {c.longest_gap:.1f} s" for c in gappy)
    return Check(name, section, Status.WARN, f"gaps over {limit:g} s: {listed}")


def _alarm_setting(config: Config, table: rvss.Table) -> Check:
    name, section = "Tension alarm setting", table.name
    if table.alarm_fs is None:
        return Check(name, section, Status.NA, f"{table.name} requires no alarms")
    limit = config.tension_member.assigned_breaking_load / table.alarm_fs
    must = f"at or below {TENSION.format(limit)} lbf (FS {table.alarm_fs:g})"
    alarm = config.monitoring.alarm_tension
    if alarm is None:
        return Check(name, section, Status.WARN, f"no [monitoring] alarm_tension; must be {must}")
    status = Status.PASS if alarm <= limit else Status.FAIL
    return Check(name, section, status, f"{TENSION.format(alarm)} lbf; must be {must}")


def alarm_point(config: Config) -> tuple[float, str] | None:
    """(tension [lbf], what it is) the checks take as the alarm point; None for a table without
    alarms
    """
    abl = config.tension_member.assigned_breaking_load
    table = config.tension_member.table
    if table.alarm_fs is None:
        return None
    point = abl / table.alarm_fs
    alarm = config.monitoring.alarm_tension
    if alarm is not None and alarm < point:
        return alarm, f"the alarm setting, {TENSION.format(alarm)} lbf (FS {abl / alarm:.2f})"
    return (
        point,
        f"{table.name}'s alarm point, {TENSION.format(point)} lbf (FS {table.alarm_fs:g})",
    )


@dataclass(frozen=True)
class Crossings:
    """A deployment's spells above a tension: when the first began, how many, and their total
    time [s]
    """

    first: np.datetime64
    spells: int
    seconds: float


def crossings(data: WinchData, cast: Cast, limit: float) -> Crossings | None:
    """The cast's spells above `limit` [lbf], each from its first sample above to the next at or
    below it; None if it never went above
    """
    over = data.tension[cast.start : cast.stop] > limit
    if not over.any():
        return None
    times = data.time[cast.start : cast.stop]
    edges = np.diff(over.astype(np.int8), prepend=np.int8(0), append=np.int8(0))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    ends = np.minimum(ends, len(times) - 1)  # a spell to the last record ends there
    seconds = float(np.sum((times[ends] - times[starts]) / np.timedelta64(1, "s")))
    return Crossings(times[starts[0]], len(starts), seconds)


def _alarm_crossings(config: Config, data: WinchData, casts) -> Check:
    table = config.tension_member.table
    name, section = "Tension below the alarm point", table.name
    point = alarm_point(config)
    if point is None:
        return Check(name, section, Status.NA, f"{table.name} requires no alarms")
    if not casts:
        return Check(name, section, Status.NA, "no deployments")
    limit, what = point
    found = [(c, x) for c in casts if (x := crossings(data, c, limit)) is not None]
    if not found:
        return Check(name, section, Status.PASS, f"every deployment below {what}")
    listed = "; ".join(
        f"cast {c.number}: {x.seconds:,.1f} s above in {x.spells} "
        f"{'spell' if x.spells == 1 else 'spells'} from {format_time(x.first, 0)}, peak "
        f"{TENSION.format(c.max_tension)} lbf (FS "
        f"{FACTOR_OF_SAFETY.format(min_fs(config, c.max_tension))})"
        for c, x in found
    )
    return Check(name, section, Status.WARN, f"above {what}: {listed}; did the alarm sound?")


def _alarms_logged(table: rvss.Table) -> Check:
    name, section = "Alarm conditions in the logged data", table.name
    if not table.alarms_logged:
        return Check(name, section, Status.NA, f"{table.name} doesn't require it")
    return Check(
        name,
        section,
        Status.INFO,
        "not in this format's records; the logging system must show them",
    )


def _display_rate(config: Config, table: rvss.Table) -> Check:
    name, section = "Tension display refresh rate", table.name
    if table.display_hz is None:
        return Check(name, section, Status.NA, f"{table.name} requires no tension display")
    rate = config.monitoring.display_rate
    must = f"{table.display_hz:g} Hz or more"
    if rate is None:
        return Check(name, section, Status.WARN, f"no [monitoring] display_rate; must be {must}")
    status = Status.PASS if rate >= table.display_hz else Status.FAIL
    trend = "; with a tension trending graph" if table.trend_graph else ""
    return Check(name, section, status, f"{rate:g} Hz; must be {must}{trend}")


def _calibration(config: Config, table: rvss.Table, on: date) -> Check:
    name, section = "Tension measuring system calibration", table.name
    if table.calibration_months is None:
        return Check(name, section, Status.NA, f"{table.name} requires no calibration")
    last = config.monitoring.last_calibration
    every = f"every {table.calibration_months} months"
    if last is None:
        return Check(name, section, Status.WARN, f"no [monitoring] last_calibration; {every}")
    due = add_months(last, table.calibration_months)
    status = Status.PASS if on <= due else Status.FAIL
    return Check(name, section, status, f"{last}, due {due}; {every}, at the selected FS's load")


def _accuracy(config: Config, table: rvss.Table) -> Check:
    name, section = "Tension measuring accuracy", table.name
    if table.accuracy_percent is None:
        return Check(name, section, Status.NA, f"{table.name} requires none")
    accuracy = config.monitoring.accuracy
    must = f"within {table.accuracy_percent:g} % of the applied load"
    if accuracy is None:
        return Check(name, section, Status.WARN, f"no [monitoring] accuracy; must be {must}")
    status = Status.PASS if accuracy <= table.accuracy_percent else Status.FAIL
    return Check(name, section, status, f"{accuracy:g} %; must be {must}")


def _sheaves(config: Config, table: rvss.Table) -> Check:
    name, section = "Sheaves and load-carrying rollers", table.name
    member = config.tension_member
    if table.min_d_ratio is None:
        return Check(name, section, Status.INFO, "as large as practicable")
    if not config.sheaves:
        return Check(name, section, Status.WARN, "no [[sheave]] in the config (A.5.2.1 logs them)")
    if member.diameter is None:
        return Check(name, section, Status.WARN, "no [tension_member] diameter (d)")
    problems, minimum = [], table.min_d_ratio * member.diameter
    if table.min_d1_ratio is not None and member.largest_wire_diameter is not None:
        minimum = max(minimum, table.min_d1_ratio * member.largest_wire_diameter)
    for sheave in config.sheaves:
        if sheave.tread_diameter < minimum:
            problems.append(f"{sheave.name}: D {sheave.tread_diameter:g} in < {minimum:.1f} in")
        if (
            table.max_groove_ratio is not None
            and sheave.groove_width is not None
            and sheave.groove_width > table.max_groove_ratio * member.diameter
        ):
            problems.append(
                f"{sheave.name}: groove {sheave.groove_width:g} in > "
                f"{table.max_groove_ratio:g}d ({table.max_groove_ratio * member.diameter:.3f} in)"
            )
    rule = f"D/d {table.min_d_ratio:g}" + (
        f" or {table.min_d1_ratio:g}d1, whichever is greater" if table.min_d1_ratio else ""
    )
    if problems:
        return Check(name, section, Status.FAIL, f"{rule}: {'; '.join(problems)}")
    unchecked = (
        ""
        if member.largest_wire_diameter is not None or not table.min_d1_ratio
        else ("; d1 not in the config")
    )
    status = Status.WARN if unchecked else Status.PASS
    return Check(name, section, status, f"{len(config.sheaves)} meet {rule}{unchecked}")


def _break_test(config: Config, table: rvss.Table, on: date) -> Check:
    name, section = "Break test interval", f"{table.name}, A.5.1.1"
    member = config.tension_member
    if table.break_test_months is None:
        return Check(name, section, Status.INFO, "tested before and after each planned use")
    if not member.break_tests:
        return Check(name, section, Status.WARN, "no [[tension_member.break_test]] in the config")
    months = table.break_test_months
    if member.abl_decreased:
        months = table.break_test_months_after_decrease
    last = member.break_tests[-1]
    due = add_months(last.date, months)
    decreased = ", after a 10 % decrease in ABL" if member.abl_decreased else ""
    status = Status.PASS if on <= due else Status.FAIL
    return Check(
        name, section, status, f"{last.date}, due {due}; every {months} months{decreased}"
    )


def _abl_retirement(config: Config) -> Check:
    name, section = "Assigned breaking load", "A.5.6"
    member = config.tension_member
    abl, nbl = member.assigned_breaking_load, member.nominal_breaking_load
    detail = f"ABL {TENSION.format(abl)} lbf, {100 * abl / nbl:.0f} % of the NBL"
    if abl < rvss.RETIRE_ABL_FRACTION * nbl:
        return Check(name, section, Status.FAIL, f"{detail}: below 50 %, retire the length")
    return Check(name, section, Status.PASS, detail)


def add_months(day: date, months: int) -> date:
    """`day` plus whole calendar months, the day clipped to the month's last"""
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise AssertionError("unreachable")

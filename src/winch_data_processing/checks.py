"""The checks of a cruise's winch data and config against RVSS Appendix A (`rvss.py`).

Each `Check` names the requirement it tests and its section, and is pass, fail, warn, info or
n/a. Fail means the data or config shows the requirement wasn't met; warn, that it can't be
shown met (something's missing from the config); info, what only the operator's records can
show. The table the selected FS falls in (A.8.1 to A.8.5) sets which checks apply.

Decisions (inferred; Appendix A doesn't say how to measure them):
- A cast's logging rate is its median sample interval's; it meets a required rate within 2 %,
  for timestamps rounded to the millisecond (20 Hz logged as 0.050 s and 0.051 s).
- Calibration and break test intervals are counted back from the last record's date (or the
  cruise's end, or today, without records), whole calendar months.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import numpy as np

from winch_data_processing import rvss
from winch_data_processing.casts import Cast
from winch_data_processing.config import Config
from winch_data_processing.quantities import FACTOR_OF_SAFETY, LOGGING_RATE, TENSION
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
        _logging_rate(table, casts),
        _logging_gaps(config, casts),
        _alarm_setting(config, table),
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

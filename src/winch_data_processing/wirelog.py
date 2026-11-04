"""The cruise's wire log: a CSV of its deployments, with what Appendix A asks each tension
member's history log to hold (A.5.2.1, A.5.3.1, A.8.3.6), and the checks as a CSV.

The wire log's header is `# keyword: value` lines (the tension member, the winch, the sheave
train, the factor of safety and the table it falls in), then a line of column names and a row a
deployment, every value formatted through `quantities.py`. Cutbacks, spooling, lubrication and
test results come from the operator's records, not the winch's data, so they aren't here.
"""

import csv
import io
import re

from winch_data_processing import __version__
from winch_data_processing import quantities as q
from winch_data_processing.assess import WinchResult
from winch_data_processing.config import Config

COLUMNS = (
    (q.CAST, lambda c: c.number),
    (q.START, lambda c: c.start_time),
    (q.END, lambda c: c.end_time),
    (q.DURATION, lambda c: c.duration),
    (q.MAX_PAYOUT, lambda c: c.max_payout),
    (q.MAX_TENSION, lambda c: c.max_tension),
    (q.MAX_TENSION_TIME, lambda c: c.max_tension_time),
    (q.PAYOUT_AT_MAX_TENSION, lambda c: c.payout_at_max_tension),
    (q.MIN_FACTOR_OF_SAFETY, None),  # ABL / max tension, from the config
    (q.SAMPLES, lambda c: c.samples),
    (q.LOGGING_RATE, lambda c: c.logging_rate),
    (q.LONGEST_GAP, lambda c: c.longest_gap),
)


def safe_name(text: str) -> str:
    """`text` with anything but letters, digits, `-` and `_` replaced by `_`, for file names"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)


def stem(config: Config) -> str:
    """The outputs' names' start: the cruise and the winch"""
    return f"{safe_name(config.cruise_id)}_{safe_name(config.winch_name)}"


def header(result: WinchResult) -> list[tuple[str, str]]:
    config, member = result.config, result.config.tension_member
    lines = [("cruise", config.cruise_id)]
    # From the config's [cruise] and [provenance], or OpenVDM's cruise; only when set
    lines += [
        (key, value)
        for key, value in (
            ("cruise_name", config.cruise_name),
            ("cruise_pi", config.cruise_pi),
            ("cruise_location", config.cruise_location),
            ("vessel", config.vessel_name),
            ("contact_institution", config.contact_institution),
            ("contact_email", config.contact_email),
        )
        if value
    ]
    lines += [
        ("winch", config.winch_name),
        ("winch_manufacturer", config.winch_manufacturer),
        ("winch_model", config.winch_model),
        ("tension_member", member.id),
        ("tension_member_kind", member.kind.value),
        ("tension_member_description", member.description),
        ("nominal_breaking_load", f"{q.TENSION.format(member.nominal_breaking_load)} lbf"),
        ("assigned_breaking_load", f"{q.TENSION.format(member.assigned_breaking_load)} lbf"),
        ("factor_of_safety", f"{member.factor_of_safety:g}"),
        ("safe_working_tension", f"{q.TENSION.format(member.safe_working_tension)} lbf"),
        ("rvss_table", f"RVSS Appendix A {member.table.name}"),
    ]
    if member.diameter is not None:
        lines.append(("diameter", f"{q.DIAMETER.format(member.diameter)} in"))
    for test in member.break_tests:
        lines.append(("break_test", f"{test.date} {q.TENSION.format(test.load)} lbf"))
    for sheave in config.sheaves:
        groove = (
            ""
            if sheave.groove_width is None
            else (f", w {q.DIAMETER.format(sheave.groove_width)} in")
        )
        ratio = (
            ""
            if member.diameter is None
            else (f", D/d {q.RATIO.format(sheave.tread_diameter / member.diameter)}")
        )
        lines.append(
            (
                "sheave",
                f"{sheave.name}: D {q.DIAMETER.format(sheave.tread_diameter)} in{groove}{ratio}",
            )
        )
    lines += [
        ("deployments", str(len(result.casts))),
        ("files", str(len(result.data.files))),
        ("written_by", f"winch-data-processing {__version__}"),
    ]
    return lines


def wire_log_text(result: WinchResult) -> str:
    abl = result.config.tension_member.assigned_breaking_load
    out = io.StringIO()
    for key, value in header(result):
        out.write(f"# {key}: {value}\n")
    # each column's unit and description, in the columns' order
    out.write(f"# field_unit: {','.join(quantity.unit or '' for quantity, _ in COLUMNS)}\n")
    out.write(f"# field_long_name: {','.join(quantity.description for quantity, _ in COLUMNS)}\n")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([quantity.name for quantity, _ in COLUMNS])
    for cast in result.casts:
        row = []
        for quantity, value in COLUMNS:
            if value is None:
                row.append(
                    quantity.format(abl / cast.max_tension if cast.max_tension > 0 else None)
                )
            else:
                row.append(quantity.format(value(cast)))
        writer.writerow(row)
    return out.getvalue()


def checks_text(result: WinchResult) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["check", "section", "status", "detail"])
    for check in result.checks:
        writer.writerow([check.name, check.section, check.status.value, check.detail])
    return out.getvalue()

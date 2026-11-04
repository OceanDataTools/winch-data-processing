"""The wire record's report (`wire-record report`): what A.5.2.1 asks across cruises, as text and
as `<NSF-ID>_wire_record.pdf`: the tension member and its break tests; the deployments since
the last break test and over the record (number, hours, deepest payout, highest tension, lowest
FS); each cruise's; and every deployment since the last break test. Built with the cruise
report's helpers (`pdf_report.py`); written invariant, so identical for the same record.
"""

from collections import defaultdict
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from winch_data_processing import __version__
from winch_data_processing import quantities as q
from winch_data_processing.clitools import plural
from winch_data_processing.pdf_report import escape, layout, table
from winch_data_processing.theme import DEFAULT_THEME, Theme
from winch_data_processing.wirelog import safe_name
from winch_data_processing.wirerecord import Deployment, Totals, WireRecord, totals

TITLE = "Wire record"


def report_path(record: WireRecord, output: Path) -> Path:
    return Path(output) / f"{safe_name(record.member_id)}_wire_record.pdf"


def _since(record: WireRecord) -> str:
    tested = record.last_break_test
    return (
        f"Since the last break test ({tested})" if tested else "Since the last break test (none)"
    )


def _totals_row(label: str, found: Totals) -> list[str]:
    return [
        label,
        f"{found.deployments:,}",
        f"{found.hours:,.1f}",
        q.MAX_PAYOUT.format(found.deepest),
        q.MAX_TENSION.format(found.highest_tension),
        q.MIN_FACTOR_OF_SAFETY.format(found.lowest_factor_of_safety),
        f"{found.cruises:,}",
    ]


TOTALS_HEADER = [
    "", "Deployments", "Hours", "Deepest [m]", "Highest tension [lbf]", "Lowest FS", "Cruises",
]  # fmt: skip


def summary(record: WireRecord) -> list[str]:
    """The report's totals as lines of text, for the terminal"""
    lines = [f"{TITLE}: {record.member_id} ({record.directory})"]
    for label, found in (
        (_since(record), totals(record.since_last_break_test())),
        ("Over the record", totals(record.deployments)),
    ):
        lines.append(
            f"  {label}: {plural(found.deployments, 'deployment')}, {found.hours:,.1f} h, deepest "
            f"{q.MAX_PAYOUT.format(found.deepest)} m, highest tension "
            f"{q.MAX_TENSION.format(found.highest_tension)} lbf, lowest FS "
            f"{q.MIN_FACTOR_OF_SAFETY.format(found.lowest_factor_of_safety)}, "
            f"{plural(found.cruises, 'cruise')}"
        )
    if record.last_break_test is None:
        lines.append("  no break test in the record: every deployment counts as since one")
    return lines


def _member(record: WireRecord) -> list[list[str]]:
    member = record.tension_member
    rows = [
        ["NSF identifier", record.member_id],
        ["Kind", str(member.get("kind", "")).replace("_", " ")],
        ["Description", member.get("description", "")],
    ]
    if "nominal_breaking_load" in member:
        rows.append(
            ["Nominal breaking load", f"{q.TENSION.format(member['nominal_breaking_load'])} lbf"]
        )
    if "factor_of_safety" in member:
        rows.append(["Factor of safety, selected", f"{member['factor_of_safety']:g}"])
    for test in member.get("break_test", []):
        rows.append(["Break test", f"{test['date']}: {q.TENSION.format(test['load'])} lbf"])
    return [row for row in rows if row[1]]


def _cruises(deployments: list[Deployment]) -> list[list[str]]:
    by_cruise: dict[tuple, list[Deployment]] = defaultdict(list)
    for deployment in deployments:
        by_cruise[(deployment.cruise, deployment.vessel, deployment.winch)].append(deployment)
    rows = []
    for (cruise, vessel, winch), found in by_cruise.items():
        sums = totals(found)
        first, last = found[0].start, found[-1].end
        rows.append([
            cruise, vessel, winch, f"{q.format_time(first, 0)[:10]} to {q.format_time(last, 0)[:10]}",
            f"{sums.deployments:,}", f"{sums.hours:,.1f}", q.MAX_PAYOUT.format(sums.deepest),
            q.MAX_TENSION.format(sums.highest_tension),
        ])  # fmt: skip
    return rows


def _deployment_rows(deployments: list[Deployment]) -> list[list[str]]:
    return [
        [
            d.cruise, d.winch, str(d.number), q.format_time(d.start, 0).replace("T", " ")[:19],
            q.DURATION.format(d.duration), q.MAX_PAYOUT.format(d.max_payout),
            q.MAX_TENSION.format(d.max_tension), q.MIN_FACTOR_OF_SAFETY.format(d.min_factor_of_safety),
        ]
        for d in deployments
    ]  # fmt: skip


def write_report(record: WireRecord, path: Path | str, theme: Theme = DEFAULT_THEME) -> Path:
    path = Path(path)
    lay = layout(theme)
    title = f"{TITLE}: {record.member_id}"
    footer = theme.footer or title
    if theme.organization:
        footer = f"{theme.organization} · {footer}"

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(lay.font, lay.small.fontSize)
        canvas.setFillColor(colors.HexColor(lay.colors.footer_text))
        canvas.drawString(lay.margin, 0.4 * inch, footer)
        canvas.drawRightString(lay.page[0] - lay.margin, 0.4 * inch, f"page {document.page}")
        canvas.restoreState()

    since = record.since_last_break_test()
    story = [
        Paragraph(escape(title), lay.title),
        Paragraph(
            escape(
                "The tension member's history log across cruises (RVSS Appendix A, A.5.2.1), "
                f"from {record.directory.name}/: the deployments of every cruise merged in. "
                "Cutbacks, spooling, lubrication, test results and terminations are the "
                f"operator's records. Written by winch-data-processing {__version__}."
            ),
            lay.small,
        ),
        Spacer(1, 8),
        KeepTogether(
            [
                Paragraph("The tension member", lay.heading),
                table(lay, None, _member(record), [0.35, 0.65]),
            ]
        ),
        Spacer(1, 10),
        KeepTogether(
            [
                Paragraph("Deployments", lay.heading),
                table(
                    lay,
                    TOTALS_HEADER,
                    [
                        _totals_row(_since(record), totals(since)),
                        _totals_row("Over the record", totals(record.deployments)),
                    ],
                    [0.28, 0.14, 0.09, 0.11, 0.14, 0.11, 0.13],
                ),
                Paragraph(
                    escape(
                        "Since the last break test counts the deployments that started on or "
                        "after its date (a test has a date, not a time). Each FS is with its "
                        "cruise's ABL."
                    ),
                    lay.small,
                ),
            ]
        ),
        Spacer(1, 10),
    ]
    if record.deployments:
        story += [
            Paragraph("By cruise", lay.heading),
            table(
                lay,
                [
                    "Cruise",
                    "Vessel",
                    "Winch",
                    "Dates",
                    "Deployments",
                    "Hours",
                    "Deepest [m]",
                    "Highest tension [lbf]",
                ],
                _cruises(record.deployments),
                [0.11, 0.15, 0.09, 0.2, 0.13, 0.08, 0.1, 0.14],
            ),
            Spacer(1, 10),
        ]
    if since:
        story += [
            Paragraph(escape(_since(record)), lay.heading),
            table(
                lay,
                [
                    "Cruise",
                    "Winch",
                    "#",
                    "Start (UTC)",
                    "Duration [s]",
                    "Max payout [m]",
                    "Max tension [lbf]",
                    "Min FS",
                ],
                _deployment_rows(since),
                [0.13, 0.1, 0.05, 0.2, 0.12, 0.13, 0.15, 0.12],
            ),
        ]
    document = SimpleDocTemplate(
        str(path),
        pagesize=lay.page,
        leftMargin=lay.margin,
        rightMargin=lay.margin,
        topMargin=lay.margin,
        bottomMargin=lay.margin,
        title=title,
        subject="Tension member history log (RVSS Appendix A, A.5.2.1)",
        creator=f"winch-data-processing {__version__}",
        invariant=True,
    )
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path

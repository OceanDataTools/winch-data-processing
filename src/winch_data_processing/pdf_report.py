"""Writes the PDF report: a title block with the verdict, the checks, the winch and tension
member, the deployments (the wire log), what was read, then the figures: the cruise's, and
each deployment's.

The figures are written as SVG files first (`reports.write_figures`), and the PDF embeds those
same SVGs as vector drawings (ReportLab with svglib), so they stay sharp at any zoom and the
SVGs can be used on their own. Values are formatted and units labelled through `quantities.py`.
The PDF is written invariant (no creation date or random ID) and without a generation time, as
the wire log is, so re-running gives an identical file. Layout and helpers are
nav-qa-processing's.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from svglib.svglib import svg2rlg

from winch_data_processing import __version__, formats
from winch_data_processing import quantities as q
from winch_data_processing.assess import WinchResult
from winch_data_processing.checks import Check, Status
from winch_data_processing.theme import DEFAULT_THEME, Theme
from winch_data_processing.wirelog import header

TITLE = "Winch wire log and RVSS Appendix A checks"
TIMELINE_HEIGHT = 4.6  # [in] tallest each figure is drawn; each also fits the frame
FIGURE_HEIGHT = 4.0
CAST_HEIGHT = 3.4
HEADING_ROOM = 0.5 * inch  # a page heading above a figure
MAX_ERRORS = 12  # unreadable files listed; the rest are counted
# The worst first: the verdict's color is the worst status among the checks
SEVERITY = (Status.FAIL, Status.WARN, Status.INFO, Status.PASS, Status.NA)

# DejaVu Sans ships with matplotlib and, unlike ReportLab's built-in base fonts, has glyphs
# such as ≤ ≥ ° used in the report text
_DEJAVU = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
DEFAULT_FONTS = (_DEJAVU / "DejaVuSans.ttf", _DEJAVU / "DejaVuSans-Bold.ttf")
PAGE_SIZES = {"letter": letter, "A4": A4}


def _register_font(path: Path) -> str:
    """ReportLab's name for a TTF, registering it once"""
    name = path.stem
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


@dataclass
class Layout:
    """A theme made concrete for ReportLab: page and frame sizes [pt], fonts and styles"""

    theme: Theme
    page: tuple[float, float]
    margin: float
    frame_width: float
    frame_height: float
    font: str
    font_bold: str
    title: ParagraphStyle
    heading: ParagraphStyle
    subheading: ParagraphStyle
    body: ParagraphStyle
    small: ParagraphStyle
    cell: ParagraphStyle
    cell_bold: ParagraphStyle

    @property
    def colors(self):
        return self.theme.colors

    def fit(self, height: float) -> float:
        """height [pt], at most what's left of the frame below a page heading"""
        return min(height, self.frame_height - HEADING_ROOM)


def layout(theme: Theme = DEFAULT_THEME) -> Layout:
    page = PAGE_SIZES[theme.page_size]
    margin = theme.margin * inch
    font = _register_font(theme.font or DEFAULT_FONTS[0])
    font_bold = _register_font(theme.bold_font or theme.font or DEFAULT_FONTS[1])
    scale = theme.font_scale
    styles = getSampleStyleSheet()
    heading2, heading3 = styles["Heading2"], styles["Heading3"]
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=9 * scale, leading=11 * scale, fontName=font
    )
    cell = ParagraphStyle("cell", parent=body, fontSize=7 * scale, leading=8.4 * scale)
    return Layout(
        theme=theme,
        page=page,
        margin=margin,
        frame_width=page[0] - 2 * margin,
        frame_height=page[1] - 2 * margin - 12,  # the frame's 6 pt top and bottom padding
        font=font,
        font_bold=font_bold,
        title=ParagraphStyle(
            "title",
            parent=styles["Title"],
            alignment=TA_LEFT,
            fontSize=16 * scale,
            leading=20 * scale,
            spaceAfter=4,
            fontName=font_bold,
        ),
        heading=ParagraphStyle(
            "heading",
            parent=heading2,
            fontSize=heading2.fontSize * scale,
            leading=heading2.leading * scale,
            spaceBefore=0,
            spaceAfter=6,
            fontName=font_bold,
        ),
        subheading=ParagraphStyle(
            "subheading",
            parent=heading3,
            fontSize=heading3.fontSize * scale,
            leading=heading3.leading * scale,
            spaceBefore=2,
            spaceAfter=4,
            fontName=font_bold,
        ),
        body=body,
        small=ParagraphStyle(
            "small",
            parent=body,
            fontSize=7 * scale,
            leading=8.5 * scale,
            textColor=colors.HexColor(theme.colors.muted_text),
        ),
        cell=cell,
        cell_bold=ParagraphStyle("cellbold", parent=cell, fontName=font_bold),
    )


def drawing(path: Path, width: float, max_height: float | None = None) -> Flowable:
    """An SVG as a vector drawing scaled to `width` (and at most `max_height`)"""
    figure = svg2rlg(str(path))
    scale = width / figure.width
    if max_height is not None:
        scale = min(scale, max_height / figure.height)
    figure.scale(scale, scale)
    figure.width *= scale
    figure.height *= scale
    figure.hAlign = "CENTER"
    return figure


def escape(text) -> str:
    """text for a Paragraph, whose mini-markup would otherwise read < > & as tags"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cell(text, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def table(
    lay: Layout,
    header_row: list[str] | None,
    rows: list[list],
    widths: list[float],
    extra_styles=(),
) -> Table:
    """A table across the frame, its header row repeated on each page (none without
    `header_row`); `widths` are fractions of its width
    """
    data = [[_cell(h, lay.cell_bold) for h in header_row]] if header_row else []
    data += [[c if isinstance(c, Flowable) else _cell(c, lay.cell) for c in row] for row in rows]
    result = Table(
        data, colWidths=[w * lay.frame_width for w in widths], repeatRows=1 if header_row else 0
    )
    header_style = (
        [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(lay.colors.table_header))]
        if header_row
        else []
    )
    result.setStyle(
        TableStyle(
            [
                *header_style,
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(lay.colors.table_grid)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                *extra_styles,
            ]
        )
    )
    return result


# -- The title block --

LOGO_HEIGHT = 0.6 * inch
LOGO_MAX_WIDTH = 2.0 * inch


def _logo(path: Path) -> Image:
    """The theme's logo, at most LOGO_HEIGHT tall and LOGO_MAX_WIDTH wide"""
    logo = Image(str(path))
    scale = min(LOGO_HEIGHT / logo.imageHeight, LOGO_MAX_WIDTH / logo.imageWidth)
    logo.drawWidth, logo.drawHeight = logo.imageWidth * scale, logo.imageHeight * scale
    return logo


def _cruise_facts(result: WinchResult) -> list[str]:
    """Cruise metadata for the title block, leaving out what isn't known"""
    config = result.config
    dates = (
        f"{config.cruise_start} to {config.cruise_end}"
        if config.cruise_start and config.cruise_end
        else ""
    )
    winch = " ".join(p for p in (config.winch_manufacturer, config.winch_model) if p)
    facts = [
        f"vessel {config.vessel_name}" if config.vessel_name else "",
        f"PI {config.cruise_pi}" if config.cruise_pi else "",
        dates,
        config.cruise_location,
        f"winch {config.winch_name}" + (f" ({winch})" if winch else ""),
        f"contact {config.contact_institution}" if config.contact_institution else "",
    ]
    return [fact for fact in facts if fact]


def _source(result: WinchResult) -> str:
    data, source = result.data, result.config.data
    where = str(result.directory) if result.directory else "no directory"
    description = formats.READERS[source.format].description
    return (
        f"Source: {where} · {len(data.files)} files ({source.files}) · format {source.format} "
        f"({description})"
    )


def verdict(checks: list[Check]) -> tuple[Status, str]:
    """The checks in a line, and the worst status among them"""
    counts = Counter(c.status for c in checks)
    worst = next((s for s in SEVERITY if counts[s]), Status.NA)
    if counts[Status.FAIL]:
        failed = [c.name for c in checks if c.status is Status.FAIL]
        text = f"{counts[Status.FAIL]} of {len(checks)} checks failed: {'; '.join(failed)}"
    elif counts[Status.WARN]:
        text = (
            f"No check failed; {counts[Status.WARN]} can't be shown met from the config "
            f"(warn), {counts[Status.PASS]} passed"
        )
    else:
        text = f"No check failed; {counts[Status.PASS]} of {len(checks)} passed"
    return worst, text


def _title(lay: Layout, result: WinchResult) -> list[Flowable]:
    config = result.config
    facts = _cruise_facts(result)
    heading = [
        Paragraph(escape(f"{TITLE}: {config.cruise_id} {config.winch_name}"), lay.title),
        Paragraph(escape(config.cruise_name or ""), lay.body),
    ]
    if lay.theme.logo is not None:
        # The title and name beside the logo, right-aligned
        logo = _logo(lay.theme.logo)
        heading = [
            Table(
                [[heading, logo]],
                colWidths=[lay.frame_width - logo.drawWidth - 6, logo.drawWidth + 6],
                style=[
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ],
            )
        ]
    worst, text = verdict(result.checks)
    banner = Table(
        [[Paragraph(escape(text), lay.cell_bold)]],
        colWidths=[lay.frame_width],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(lay.colors.status[worst])),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor(lay.colors.table_grid)),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ],
    )
    return [
        *heading,
        Spacer(1, 4),
        *([Paragraph(escape(" · ".join(facts)), lay.small)] if facts else []),
        Paragraph(escape(_source(result)), lay.small),
        Paragraph(escape(f"Written by winch-data-processing {__version__}"), lay.small),
        Spacer(1, 6),
        banner,
        Spacer(1, 10),
    ]


# -- Checks, the winch, the deployments, the records --


def checks_table(lay: Layout, checks: list[Check]) -> Table:
    rows = [[c.name, c.section, c.status.value, c.detail] for c in checks]
    styles = [
        ("BACKGROUND", (2, i), (2, i), colors.HexColor(lay.colors.status[c.status.value]))
        for i, c in enumerate(checks, start=1)
    ]
    widths = [0.27, 0.17, 0.08, 0.48]
    return table(lay, ["Check", "Section", "Status", "Detail"], rows, widths, styles)


# The wire log's header lines shown in "The winch and tension member", by key
WINCH_FACTS = {
    "winch": "Winch",
    "winch_manufacturer": "Manufacturer",
    "winch_model": "Model",
    "tension_member": "Tension member (NSF identifier)",
    "tension_member_kind": "Kind",
    "tension_member_description": "Description",
    "nominal_breaking_load": "Nominal breaking load (NBL)",
    "assigned_breaking_load": "Assigned breaking load (ABL)",
    "factor_of_safety": "Factor of safety, selected",
    "safe_working_tension": "Safe working tension (SWT = ABL / FS)",
    "rvss_table": "Table",
    "diameter": "Diameter (d)",
    "break_test": "Break test",
    "sheave": "Sheave",
}


def _winch(lay: Layout, result: WinchResult) -> Table:
    rows = [
        [WINCH_FACTS[key], value] for key, value in header(result) if key in WINCH_FACTS and value
    ]
    monitoring = result.config.monitoring
    if monitoring.alarm_tension is not None:
        rows.append(["Alarm setting", f"{q.TENSION.format(monitoring.alarm_tension)} lbf"])
    return table(lay, None, rows, [0.35, 0.65])


def _time(value) -> str:
    """A UTC time to the second, as the deployments table shows it (its header says UTC)"""
    return q.format_time(value, 0).replace("T", " ").removesuffix("Z")


def _deployments(lay: Layout, result: WinchResult) -> list[Flowable]:
    """The wire log's deployments, its columns' short names (the CSV has their descriptions)"""
    if not result.casts:
        return [Paragraph("No deployments were found.", lay.body)]
    abl = result.config.tension_member.assigned_breaking_load
    header_row = [
        "#", "Start (UTC)", "End (UTC)", "Duration [s]", "Max payout [m]", "Max tension [lbf]",
        "Payout at max tension [m]", "Min FS", "Logging rate [Hz]", "Longest gap [s]",
    ]  # fmt: skip
    rows = [
        [
            cast.number,
            _time(cast.start_time),
            _time(cast.end_time),
            q.DURATION.format(cast.duration),
            q.MAX_PAYOUT.format(cast.max_payout),
            q.MAX_TENSION.format(cast.max_tension),
            q.PAYOUT_AT_MAX_TENSION.format(cast.payout_at_max_tension),
            q.MIN_FACTOR_OF_SAFETY.format(
                abl / cast.max_tension if cast.max_tension > 0 else None
            ),
            q.LOGGING_RATE.format(cast.logging_rate),
            q.LONGEST_GAP.format(cast.longest_gap),
        ]
        for cast in result.casts
    ]
    widths = [0.04, 0.15, 0.15, 0.09, 0.09, 0.09, 0.11, 0.08, 0.1, 0.1]
    return [
        Paragraph(
            "The wire log's deployments; the full log, with the time of each peak, is the "
            "_wire_log.csv beside this report. Each deployment includes its padding, the lift "
            "and the recovery.",
            lay.small,
        ),
        table(lay, header_row, rows, widths),
    ]


def _records(lay: Layout, result: WinchResult) -> list[Flowable]:
    data = result.data
    rows = [
        ["Files read", f"{len(data.files):,}"],
        ["Records read, within the cruise's dates", f"{data.records:,}"],
    ]
    if data.outside:
        rows.append(["Records outside the cruise's dates", f"{data.outside:,}"])
    if data.backwards:
        rows.append(["Records whose time went back, as logged (sorted)", f"{data.backwards:,}"])
    skipped = Counter()
    for reasons in data.skipped.values():
        skipped.update(reasons)
    rows += [[f"Lines skipped: {reason}", f"{count:,}"] for reason, count in skipped.most_common()]
    if len(data.names) > 1 or (result.config.record_name and data.names):
        names = ", ".join(f"{name} {count:,}" for name, count in data.names.most_common())
        rows.append(["Records by the winch name they carry", names])
    flowables: list[Flowable] = [table(lay, None, rows, [0.55, 0.45])]
    if data.errors:
        listed = data.errors[:MAX_ERRORS]
        more = len(data.errors) - len(listed)
        text = "; ".join(listed) + (f"; and {more} more" if more else "")
        flowables += [
            Spacer(1, 4),
            Paragraph(escape(f"Files that couldn't be read: {text}"), lay.small),
        ]
    return flowables


# -- The figures --


def _figures(lay: Layout, result: WinchResult, svgs: dict[str, Path]) -> list[Flowable]:
    """The figures, the cruise's then each deployment's. No forced page breaks: each starts a
    new page only when it doesn't fit in what's left of the current one
    """
    width = lay.frame_width

    def figure(heading: str, name: str, height: float, style=None, above=()) -> Flowable:
        return KeepTogether(
            [
                *above,
                Paragraph(escape(heading), style or lay.heading),
                drawing(svgs[name], width, max_height=lay.fit(height * inch)),
                Spacer(1, 10),
            ]
        )

    story = [
        figure("Timeline", "timeline", TIMELINE_HEIGHT),
        figure("Tension against payout", "tension_and_payout", FIGURE_HEIGHT),
        figure("Factor of safety", "factor_of_safety", FIGURE_HEIGHT),
    ]
    for i, cast in enumerate(result.casts):
        # The section's heading kept with its first figure
        above = [Paragraph("Each deployment", lay.heading)] if i == 0 else []
        name = f"cast_{cast.number:03d}"
        story.append(figure(f"Deployment {cast.number}", name, CAST_HEIGHT, lay.subheading, above))
    return story


def write_pdf(
    result: WinchResult, path: Path | str, svgs: dict[str, Path], theme: Theme = DEFAULT_THEME
) -> Path:
    """Writes the PDF, embedding the figures' SVGs (`reports.write_figures`, by name)"""
    path = Path(path)
    lay = layout(theme)
    config = result.config
    title = f"{TITLE}: {config.cruise_id} {config.winch_name}"
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

    document = SimpleDocTemplate(
        str(path),
        pagesize=lay.page,
        leftMargin=lay.margin,
        rightMargin=lay.margin,
        topMargin=lay.margin,
        bottomMargin=lay.margin,
        title=title,
        subject="Wire log and RVSS Appendix A checks",
        creator=f"winch-data-processing {__version__}",
        invariant=True,  # no creation date or random ID: identical run to run
    )
    story = [
        *_title(lay, result),
        KeepTogether([Paragraph("Checks", lay.heading), checks_table(lay, result.checks)]),
        Spacer(1, 10),
        KeepTogether(
            [Paragraph("The winch and tension member", lay.heading), _winch(lay, result)]
        ),
        Spacer(1, 10),
        Paragraph("Deployments", lay.heading),
        *_deployments(lay, result),
        Spacer(1, 10),
        KeepTogether([Paragraph("Records", lay.heading), *_records(lay, result)]),
        Spacer(1, 10),
        *_figures(lay, result, svgs),
    ]
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path

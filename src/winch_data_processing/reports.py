"""Everything a run writes, for a config's stem (`wirelog.stem`: `<CRUISE>_<WINCH>`): the wire
log, `<stem>_wire_log.csv`; the checks, `<stem>_checks.csv`; every figure as an SVG in
`<stem>_plots/` (`plots.FIGURES`, then `cast_NNN.svg` for each deployment); and the PDF report,
`<stem>_report.pdf`, which embeds them. A deployment's SVG left from an earlier run with more
deployments is removed, so the directory matches the log. All are identical run to run.
"""

from pathlib import Path

import matplotlib
from matplotlib.figure import Figure

from winch_data_processing import plots
from winch_data_processing.assess import WinchResult
from winch_data_processing.config import Config
from winch_data_processing.pdf_report import write_pdf
from winch_data_processing.theme import Theme, resolve_theme
from winch_data_processing.wirelog import checks_text, stem, wire_log_text

CAST_SVG = "cast_*.svg"


def report_paths(config: Config, output: Path) -> dict[str, Path]:
    """What write_reports writes: "wire log", "checks", "plots" (the figures' directory) and
    "pdf"
    """
    base = stem(config)
    return {
        "wire log": Path(output) / f"{base}_wire_log.csv",
        "checks": Path(output) / f"{base}_checks.csv",
        "plots": Path(output) / f"{base}_plots",
        "pdf": Path(output) / f"{base}_report.pdf",
    }


def save_svg(figure: Figure, path: Path) -> Path:
    """The figure as SVG. No date in the metadata, and a fixed salt for matplotlib's clip path
    IDs (random by default), so re-running gives identical files
    """
    with matplotlib.rc_context({"svg.hashsalt": "winch-data-processing"}):
        figure.savefig(path, format="svg", metadata={"Date": None})
    return path


def write_figures(result: WinchResult, directory: Path, theme: Theme) -> dict[str, Path]:
    """Every figure as `<name>.svg` in directory, by name: the cruise's then each deployment's"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = {
        name: save_svg(make(result, theme), directory / f"{name}.svg")
        for name, make in plots.FIGURES.items()
    }
    written |= {
        name: save_svg(figure, directory / f"{name}.svg")
        for name, figure in plots.cast_figures(result, theme)
    }
    for stale in set(directory.glob(CAST_SVG)) - set(written.values()):
        stale.unlink()
    return written


def write_reports(
    result: WinchResult,
    output: Path,
    theme: Theme | None = None,
    theme_file: Path | None = None,
) -> list[Path]:
    """The wire log, the checks, the figures and the PDF. The look is `theme`, or the
    `theme_file` (a --theme file) with the config's [theme] on top
    """
    if theme is None:
        theme = resolve_theme(theme_file, result.config)
    paths = report_paths(result.config, output)
    Path(output).mkdir(parents=True, exist_ok=True)
    paths["wire log"].write_text(wire_log_text(result))
    paths["checks"].write_text(checks_text(result))
    svgs = write_figures(result, paths["plots"], theme)
    write_pdf(result, paths["pdf"], svgs, theme)
    return [paths["wire log"], paths["checks"], *svgs.values(), paths["pdf"]]

"""Writes a Jupyter notebook that re-runs a cruise's assessment step by step: the config,
`assess`, the checks, the wire log, the records, every figure, and writing the reports.

The cells are `notebook_template.py`, a Python file Ruff checks, with markers this module fills
for the run; `tests/test_notebook.py` executes the result. Ported from nav-qa-processing's,
without its integrations' extras. Cells get IDs by position, not nbformat's random ones, so an
unexecuted notebook is identical run to run.
"""

import re
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from winch_data_processing import __version__
from winch_data_processing.assess import WinchResult

if TYPE_CHECKING:
    import nbformat

# The notebook's cells, as Python; see its docstring for the markers
TEMPLATE = files("winch_data_processing") / "notebook_template.py"
MARKER = re.compile(r"^.*# @(?P<name>\w+)$")


class NotebookError(RuntimeError):
    """The notebook can't be executed (nbclient and ipykernel missing)"""


def _template() -> list[tuple[str, list[str]]]:
    """notebook_template.py's cells, (kind, lines), in order"""
    cells: list[tuple[str, list[str]]] = []
    for line in TEMPLATE.read_text().splitlines():
        if line.startswith("# %%"):
            cells.append(("markdown" if line == "# %% [markdown]" else "code", []))
        elif cells:
            cells[-1][1].append(line)
    return cells


def _code(lines: list[str], code: dict[str, str]) -> str:
    """A template code cell with its markers filled"""
    out: list[str] = []
    for line in lines:
        if line.startswith("# @raw "):
            out.append(line.removeprefix("# @raw "))
        elif marker := MARKER.match(line):
            out.append(code[marker["name"]])
        else:
            out.append(line)
    return "\n".join(out).strip("\n")


def _text(lines: list[str], values: dict[str, str]) -> str:
    """A template markdown cell with its `{name}`s filled"""
    text = "\n".join(line.removeprefix("#").removeprefix(" ") for line in lines).strip("\n")
    for name, value in values.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def _path(name: str, path: Path | None, without: str) -> str:
    return (
        f"{name} = Path({str(Path(path).resolve())!r})" if path else f"{name} = None  # {without}"
    )


def build_notebook(
    result: WinchResult, output_dir: Path, theme_file: Path | None = None
) -> "nbformat.NotebookNode":
    """The notebook in notebook_template.py, for this run (theme_file is the --theme file the
    reports were written with, if any)
    """
    # Here, not at the top: the CLI imports this module for NotebookError
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

    config = result.config
    values = {
        "cruise_id": config.cruise_id,
        "winch_name": config.winch_name,
        "cruise_name": config.cruise_name,
        "data_format": config.data.format,
        "version": __version__,
    }
    code = {
        "config_path": _path("CONFIG", config.path, "the run's config had no file: its path"),
        "data_dir": _path("DATA_DIR", result.directory, "the winch's raw files"),
        "theme_file": _path(
            "THEME_FILE", theme_file, "a --theme file; the config's [theme] applies on top"
        ),
        "output_dir": _path("OUTPUT_DIR", output_dir, "where the reports go"),
    }
    cells = []
    for i, (kind, lines) in enumerate(_template()):
        cell = (
            new_markdown_cell(_text(lines, values))
            if kind == "markdown"
            else new_code_cell(_code(lines, code))
        )
        cell.id = f"cell-{i}"
        cells.append(cell)
    notebook = new_notebook(cells=cells)
    notebook.metadata["kernelspec"] = {
        "name": "python3",
        "display_name": "Python 3",
        "language": "python",
    }
    notebook.metadata["language_info"] = {"name": "python"}
    return notebook


def write_notebook(
    result: WinchResult,
    path: Path | str,
    output_dir: Path | str,
    execute: bool = False,
    theme_file: Path | None = None,
) -> Path:
    """Writes the notebook; with `execute`, runs it first and saves its outputs (needs nbclient
    and ipykernel, in the dev group). The last cell, which writes the reports, isn't run: they
    were just written
    """
    import nbformat

    path = Path(path)
    notebook = build_notebook(result, Path(output_dir), theme_file)
    if execute:
        try:
            from nbclient import NotebookClient
        except ImportError as error:
            raise NotebookError(
                "executing the notebook needs nbclient and ipykernel: uv sync (with the dev group)"
            ) from error
        write = notebook.cells.pop()
        NotebookClient(
            notebook,
            timeout=1800,
            record_timing=False,  # no times in the saved notebook
            resources={"metadata": {"path": str(path.parent)}},
        ).execute()
        notebook.cells.append(write)
    nbformat.write(notebook, path)
    return path

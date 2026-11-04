"""The notebook that re-runs the assessment (notebook.py, notebook_template.py), executed."""

import sys
from datetime import timedelta

import nbformat
import pytest
from conftest import CONFIG, START, cast, write_cruise

from winch_data_processing.assess import assess
from winch_data_processing.cli import main
from winch_data_processing.config import load_config
from winch_data_processing.notebook import build_notebook
from winch_data_processing.pdf_report import verdict
from winch_data_processing.reports import report_paths, write_reports


@pytest.fixture
def run(tmp_path):
    first = cast(START, max_payout=500)
    second = cast(first[-1][0] + timedelta(minutes=30), max_payout=800)
    data = write_cruise(tmp_path / "data", first, second)
    config = tmp_path / "TEST01_CTD.toml"
    config.write_text(CONFIG)
    return data, config, tmp_path / "output"


def test_the_notebook_names_this_runs_paths(run):
    data, config, output = run
    result = assess(data, load_config(config))
    notebook = build_notebook(result, output)
    sources = [cell.source for cell in notebook.cells]
    assert sources[0].startswith("# Winch wire log and RVSS Appendix A checks: TEST01 CTD")
    assert "format openrvdas_lci90i" in sources[0]
    paths = sources[1]
    assert f"CONFIG = Path({str(config.resolve())!r})" in paths
    assert f"DATA_DIR = Path({str(data.resolve())!r})" in paths
    assert (
        "THEME_FILE = None" in paths and f"OUTPUT_DIR = Path({str(output.resolve())!r})" in paths
    )
    assert sources[2].startswith("%matplotlib inline")
    assert [cell.id for cell in notebook.cells] == [f"cell-{i}" for i in range(len(sources))]


def test_an_unexecuted_notebook_is_identical_run_to_run(run):
    data, config, output = run
    result = assess(data, load_config(config))
    notebook = report_paths(result.config, output)["notebook"]
    write_reports(result, output)
    first = notebook.read_bytes()
    write_reports(result, output)
    assert notebook.read_bytes() == first


def test_the_executed_notebook_reruns_the_assessment(run):
    data, config, output = run
    theme = run[2].parent / "theme.toml"
    theme.write_text('palette = "colorblind"\n')
    command = ["run", str(data), "-c", str(config), "-o", str(output), "--theme", str(theme)]
    assert main([*command, "--execute-notebook"]) == 0
    result = assess(data, load_config(config))
    notebook = nbformat.read(report_paths(result.config, output)["notebook"], as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    assert f"THEME_FILE = Path({str(theme.resolve())!r})" in code[0].source

    def text(cell) -> str:
        return "".join(o.get("text", "") for o in cell.outputs if o.output_type == "stream")

    assert text(code[1]).strip() == verdict(result.checks)[1]
    assert "2 deployments" in text(code[2])
    assert "# tension_member: NSF-TEST" in text(code[3])
    images = [o for o in code[5].outputs if "image/png" in o.get("data", {})]
    assert len(images) == 3 + 2  # the cruise's figures, then each deployment's
    assert not code[-1].outputs  # writing the reports isn't run: they were just written
    assert all("ename" not in o for cell in code for o in cell.outputs)


def test_executing_without_nbclient_is_one_line(run, monkeypatch, capsys):
    data, config, output = run
    monkeypatch.setitem(sys.modules, "nbclient", None)  # import nbclient raises ImportError
    command = ["run", str(data), "-c", str(config), "-o", str(output), "--execute-notebook"]
    assert main(command) == 1
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1 and "needs nbclient and ipykernel" in err

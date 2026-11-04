"""The notebook `notebook.build_notebook` writes, as a Python file (nav-qa-processing's #144), so
Ruff checks its code and renaming a function it calls is caught here. It's read as text, never
imported.

Cells are in jupytext's percent format: `# %%` starts a code cell, `# %% [markdown]` a text
cell, whose lines are written after `# `. Everything before the first cell is this header.
In a code cell, a line ending in `# @name` is replaced by the run's code for `name`, and
`# @raw TEXT` becomes TEXT (for what isn't Python, such as a magic). In text, `{name}` is the
run's value.
"""

# %% [markdown]
# # Winch wire log and RVSS Appendix A checks: {cruise_id} {winch_name}
#
# {cruise_name}
#
# Re-runs the assessment behind this cruise's wire log and PDF report: it reads the winch's raw files (format {data_format}), finds the deployments, and checks the data and the config against RVSS Appendix A. Edit the paths, or the config's `[thresholds]` or tension member, and re-run to change the assessment.
#
# Written by winch-data-processing {version}.

# %%
from pathlib import Path

# Inputs and outputs
CONFIG = Path()  # @config_path
DATA_DIR = Path()  # @data_dir
THEME_FILE = None  # @theme_file
OUTPUT_DIR = Path()  # @output_dir

# %%
# @raw %matplotlib inline
from IPython.display import display

from winch_data_processing import plots, reports, wirelog
from winch_data_processing.assess import assess
from winch_data_processing.cli import summary
from winch_data_processing.config import load_config
from winch_data_processing.pdf_report import verdict
from winch_data_processing.theme import resolve_theme

config = load_config(CONFIG)
result = assess(DATA_DIR, config)
theme = resolve_theme(THEME_FILE, result.config)
print(verdict(result.checks)[1])  # the checks in a line

# %% [markdown]
# ## Checks
#
# Each requirement of Appendix A the data or the config can show, with its section. Fail: the data or the config shows it wasn't met; warn: it can't be shown met (something's missing from the config); info: what only the operator's records can show.

# %%
print("\n".join(summary(result)))

# %% [markdown]
# ## The wire log
#
# Its header (the tension member, the winch and its sheaves) and a row a deployment, as the `_wire_log.csv` has them.

# %%
print(wirelog.wire_log_text(result))

# %% [markdown]
# ## Records
#
# What was read, and the lines the reader couldn't use, by file and reason.

# %%
data = result.data
print(f"{data.records:,} records from {len(data.files)} files; {len(data):,} kept (idle.py)")
print(f"{data.outside:,} outside the cruise's dates; {data.backwards:,} whose time went back")
for name, reasons in data.skipped.items():
    print(f"{name}: {dict(reasons)}")
for error in data.errors:
    print(f"couldn't be read: {error}")

# %% [markdown]
# ## Figures
#
# The cruise's, then each deployment's, as the reports have them.

# %%
for make in plots.FIGURES.values():
    display(make(result, theme))
for _, figure in plots.cast_figures(result, theme):
    display(figure)

# %% [markdown]
# ## Write the reports
#
# The wire log, the checks, the figures and the PDF, into `OUTPUT_DIR`, replacing them.

# %%
written = reports.write_reports(result, OUTPUT_DIR, theme_file=THEME_FILE, notebook=False)
for path in written:
    print(path)

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`winch-data-processing` reads oceanographic winch data (tension, payout, speed), finds the
deployments, writes each cruise's wire log and checks it against **RVSS Appendix A**, "UNOLS Rope
and Cable Safe Working Standards" (RVSS 11th edition, Appendix A last updated 25 June 2026:
https://www.unols.org/sites/default/files/2026-10/RVSS_11_Master_Updated_17Jul2026.pdf). Follow
the adopted text, not the RVTEC 2008 overview of the draft. `docs/rvss-appendix-a.md` maps each
requirement to the data, the config or the operator's records; `docs/architecture.md` has the
modules, decisions and phased plan. Keep both in sync when the design changes.

It follows the sibling projects `../nav-qa-processing` and `../sbe-qa-processing`. When this
file doesn't settle a structural or tooling question, do what nav-qa-processing does: read its
`CLAUDE.md`, `pyproject.toml` and `src/nav_qa_processing/` (`quantities.py`, `config.py`,
`cli.py`, `reports.py`, `pdf_report.py`, `notebook.py`, `integrations/openvdm/`).

What exists (Phases 1 and 2):
- `rvss.py`: Appendix A as data (`Kind`, `Table` A.8.1 to A.8.5, `MIN_FS`, `ELASTIC_LIMIT_FS`,
  `table_for`). Change a requirement here, citing its section, never in a check.
- `config.py`: the config TOML (`configs/config.example.toml` shows every key; add a new key
  there and to `SCHEMA` and the README's table). ABL = min(NBL, latest TBL); SWT = ABL / FS.
- `units.py`, `quantities.py`: tension in lbf, payout m, speed m min-1, diameters in; every
  written value through `quantities.py`.
- `formats/`: `READERS`; `lci90i` and `openrvdas_lci90i` (the LCI-90i's `RD` record and its
  byte-sum checksum, from RR2403: R2R filesets 160178 and 160181, Revelle's hydro and trawl
  winches, R2R format 100038), `delimited`. The LCI-90i reader has a fixed-width fast path
  and a per-line path that must decide the same (`docs/architecture.md`, Decisions). RR2403's
  one cast is `tests/data/` (its README) and `tests/test_rr2403.py`; the other winch formats
  need sample files.
- `rawdata.py`, `idle.py` (drops the idle records `find_casts` doesn't need, as files are read:
  a change to `find_casts`'s rule needs one here, and `tests/test_idle.py` to agree),
  `casts.py`, `checks.py`, `assess.py`, `wirelog.py`, `cli.py` (`run`, `formats`, and the
  integrations' commands), `clitools.py`, `network.py`.
- Phase 2: Table A.8.4's slow-haul checks (`checks.slow_hauls`: the halts every ~500 m once FS 2.0
  is reached, rules in `checks.py`'s docstring), `plots.py` (figures from a `WinchResult` and a
  `Theme`, without pyplot), `theme.py` (nav-qa-processing's keys, so one theme file serves the
  three projects; the config's `[theme]`), `pdf_report.py` (ReportLab with svglib,
  nav-qa-processing's `Layout`), `reports.py` (`write_reports`: everything a run writes, for `run`
  and the hook; every output identical run to run: SVGs with a fixed hash salt and no date, the PDF
  invariant, the notebook's cell IDs by position), `notebook.py` with `notebook_template.py` (Ruff
  checks the template's code; `tests/test_notebook.py` executes it). pypdf, nbclient and ipykernel
  are dev dependencies; matplotlib, ReportLab and nbformat load only when reports are written.
- Phase 4 (in progress, `docs/architecture.md`'s plan): `wirerecord.py`, the wire record
  (`RECORDS_DIR/<NSF-ID>/deployments.csv` and `tension_member.toml`, plain files read and
  written only there; `events.toml`, the operator's, read and checked there, never written),
  merged by `run --wire-record` and the hook, whose checks then use the record's break tests
  (`with_break_tests`); `record_report.py`, `wire-record report`.
- `integrations/openvdm/` (Phase 5, done early): the `openvdm` post-transfer hook,
  `config-from-openvdm` and `site-config`, ported from nav-qa-processing's. The site TOML's
  `[[winch]]` tables are config TOMLs but `[cruise]` (`configs/site.example.toml`); OpenVDM's
  cruise fills `[cruise]`. Setup is in `INSTALL.md`, whose `openvdm.yaml` block
  `tests/test_openvdm_contract.py` runs through OpenVDM 2.17's own code (`../openvdm` with a
  `venv/`, or `OPENVDM_SOURCE`; skipped without). Keep the hook's arguments' names: installed
  `openvdm.yaml` files call them.

## Tooling

- uv, hatchling, `src/` layout, package `winch_data_processing`, console script
  `winch-data-processing`. Python ≥3.11. Commit `uv.lock` with each dependency change.
- Tests: `uv run pytest -n auto`; plain pytest functions on synthetic casts (`tests/conftest.py`:
  `cast`, `lci_lines`, `write_cruise`, `CONFIG`). The OpenVDM hook's against a stand-in API
  (`tests/test_openvdm.py`). pyyaml is the `openvdm` extra and a dev dependency.
- Lint and format: `uv run ruff check .` and `uv run ruff format --check .` (line length 99).
- `/data/` (raw winch data) and `/output/` are gitignored; `configs/` is the users' space but for
  `config.example.toml`, `site.example.toml` and `events.example.toml`.

## Conventions (from nav-qa-processing)

- Modern Python, dataclasses, short docstrings without `:param:` boilerplate, units in brackets
  in comments (`# [lbf]`).
- One entry point (`assess.assess`) returns a result every writer takes.
- Per-file failures are recorded on the result, not raised; a reader counts what it skips.
- Units are CF/UDUNITS strings; never put format strings or unit names in writers.
- Inferred rules (what a deployment is, how a rate is measured) are stated in the module's
  docstring and in `docs/architecture.md` ("Decisions").
- CLI: `-o` a directory, `--force` to overwrite (checked before any work), uppercase metavars,
  `-v`/`-vv` on every parser, expected errors one line on stderr and exit 1.

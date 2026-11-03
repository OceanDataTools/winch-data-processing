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

What exists (Phase 1):
- `rvss.py`: Appendix A as data (`Kind`, `Table` A.8.1 to A.8.5, `MIN_FS`, `ELASTIC_LIMIT_FS`,
  `table_for`). Change a requirement here, citing its section, never in a check.
- `config.py`: the config TOML (`configs/config.example.toml` shows every key; add a new key
  there and to `SCHEMA` and the README's table). ABL = min(NBL, latest TBL); SWT = ABL / FS.
- `units.py`, `quantities.py`: tension in lbf, payout m, speed m min-1, diameters in; every
  written value through `quantities.py`.
- `formats/`: `READERS`; `lci90i` and `openrvdas_lci90i` (the LCI-90i's `RD` record and its
  byte-sum checksum, from RR2403: R2R filesets 160178 and 160181, Revelle's hydro and trawl
  winches, R2R format 100038), `delimited`. RR2403's one cast is `tests/data/` (its README) and
  `tests/test_rr2403.py`; the other winch formats need sample files.
- `rawdata.py`, `casts.py`, `checks.py`, `assess.py`, `wirelog.py`, `cli.py` (`run`,
  `formats`, and the integrations' commands), `clitools.py`, `network.py`.
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
  `config.example.toml` and `site.example.toml`.

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

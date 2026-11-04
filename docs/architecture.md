# Architecture and plan

winch-data-processing reads an oceanographic winch's raw tension, payout and speed records,
finds its deployments, writes the tension member's wire log for the cruise, and checks the
cruise against RVSS Appendix A. It follows the conventions of its siblings
[nav-qa-processing](https://github.com/OceanDataTools/nav-qa-processing) and
[sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing): uv, hatchling, a `src/`
layout, TOML configs, one entry point returning a result every writer takes, a project-wide
quantity table, an OpenVDM hook built as theirs are, and (from Phase 2) a PDF report, SVG figures
and a notebook that re-runs the assessment.

What Appendix A requires, and which of it the data or the config can show, is in
[rvss-appendix-a.md](rvss-appendix-a.md).

## Pipeline

```
raw files --formats/<reader>--> Records (per file, the data's units)
          --rawdata.read_fileset--> WinchData (one series, lbf / m / m min-1, sorted, cruise days)
          --casts.find_casts--> [Cast] (deployments and their figures)
          --checks.run_checks--> [Check] (pass / fail / warn / info / n/a, by section)
          = assess.assess -> WinchResult
          --reports.write_reports--> <CRUISE>_<WINCH>_wire_log.csv, <CRUISE>_<WINCH>_checks.csv,
                                     <CRUISE>_<WINCH>_plots/*.svg (plots.py, theme.py),
                                     <CRUISE>_<WINCH>_report.pdf (pdf_report.py)
```

## Modules

- `quantities.py`: every value written, defined once (name, description, datatype, CF/UDUNITS
  unit, fixed decimals); writers format only through it, as in nav-qa-processing.
- `units.py`: the units data and configs may use and their factors: tension in lbf (the Wire
  Pool's), payout in m, speed in m min-1, diameters in inches.
- `rvss.py`: Appendix A as data: `Kind` (cable, wire rope, synthetic), `Table` (A.8.1 to A.8.5:
  FS range, monitoring, alarms, calibration, accuracy, sheaves, break tests), `MIN_FS`,
  `ELASTIC_LIMIT_FS`, `table_for(kind, fs)`.
- `config.py`: the config TOML: `[cruise]`, `[winch]`, `[tension_member]` (with
  `[[tension_member.break_test]]`; `assigned_breaking_load`, `safe_working_tension`,
  `abl_decreased`, `table`), `[[sheave]]`, `[monitoring]`, `[data]` (`[data.columns]`),
  `[thresholds]`. Unknown keys are an error saying where they're allowed; an FS the standard
  forbids for the kind is refused.
- `formats/`: `READERS` (format ID -> `Reader`), each reader `(lines, DataSource, record name)
  -> Records`, counting what it skips by reason: `lci90i` and `openrvdas_lci90i`
  (`lci90i.py`: `RD` records, checksums, split records; the record from RR2403), `delimited`
  (`delimited.py`).
- `rawdata.py`: `read_fileset(directory, config)` -> `WinchData`; a file that can't be read is
  recorded on the result, not raised.
- `casts.py`: `find_casts(data, thresholds)` -> `[Cast]` (the rule in its docstring).
- `checks.py`: `run_checks(config, data, casts)` -> `[Check]` (decisions in its docstring).
- `assess.py`: `assess(directory, config)` -> `WinchResult`.
- `wirelog.py`: the wire log and checks CSVs' text.
- `plots.py`: the figures (`FIGURES`, then `cast_figures`), each `(WinchResult, Theme)` ->
  matplotlib `Figure`, made without pyplot.
- `theme.py`: the look (`--theme` file, then the config's `[theme]`), nav-qa-processing's keys.
- `pdf_report.py`: `write_pdf(result, path, svgs, theme)`: the title and verdict, checks, winch,
  deployments, records, then the figures' SVGs as vector drawings (ReportLab, svglib);
  `Layout` and the helpers are nav-qa-processing's. Written invariant, so identical run to run.
- `notebook.py`, `notebook_template.py`: the notebook that re-runs the assessment, its cells a
  Python file in jupytext's percent format that Ruff checks, with markers `build_notebook`
  fills (nav-qa-processing's, without its integrations' extras); cell IDs by position.
- `wirerecord.py`: the wire record, a tension member's deployments across cruises
  (`merge(records, result)`, `load(directory)` -> `WireRecord`, `totals`); the only module that
  reads or writes it. `record_report.py`: its report, text and PDF.
- `reports.py`: `write_reports(result, output, theme)`, everything a run writes
  (`report_paths`); the CLI and the hook both call it.
- `cli.py`: `run`, `formats`, and the integrations' commands (`INTEGRATIONS`: each module's
  `add_commands` and `EXPECTED_ERRORS`; the one place core meets them). `clitools.py`: what
  the commands share (refusing to overwrite, writing a config TOML). `network.py`: an
  unreachable server as a one-line `NetworkError`.
- `config.py` also writes TOML (`dumps_toml`), for the configs the hook makes.

### Integrations

`integrations/` builds on the core assessment; core modules never import from it, but for
`cli.py`. As in nav-qa-processing and sbe-qa-processing:

- `integrations/openvdm/hook.py`: the site TOML (`SiteConfig`, `SiteWinch`,
  `load_site_config`, `site_config_to_toml`), OpenVDM's cruise (`fetch_cruise_config` from its
  API, or `load_cruise_json` from a cruise's `cruise_config.json`), the vessel block
  (`vessel_settings`, `cruise_vessel`), `resolve` (a winch and a cruise -> `HookRun`: its
  `Config`, the config TOML's tables, the transfer's and extra directory's paths, notes),
  `cruise_toml`, `has_winch_changes`, `written_within`, `queue_md5_update`, `chown_tree`.
- `integrations/openvdm/cli.py`: `openvdm` (the hook), `config-from-openvdm`, `site-config`.
- `integrations/openvdm/site_setup.py`: `site-config`'s prompts.

## Decisions

- **Tension in lbf.** The Wire Pool's breaking loads and most fleet tensiometers are in pounds;
  data and configs in N, kN or kgf are converted on the way in.
- **A deployment** is where the payout crosses above `deploy_payout` and back, continuously (a
  stale counter or a reset isn't a crossing: RR2403's hydro counter read 664.5 m for a week),
  merged across short dips, dropped if short, and padded to take the lift and recovery
  (`casts.py`). Appendix A doesn't
  define one; the thresholds are configurable and the rule is stated in `casts.py`'s
  docstring.
- **The FS is the operator's selection**, as Appendix A says (A.5.1.1); the checks test the
  data against it and against the kind's lowest allowed FS separately, so a cruise run at
  FS 2.5 that saw FS 2.2 fails "within the SWT" but not "the lowest FS allowed".
- **Logging rate** is the median interval's, within 2 % (millisecond timestamps).
- **The site TOML holds the winches.** OpenVDM knows the cruise, not the winches, so each is a
  `[[winch]]` table holding a config TOML's tables but `[cruise]`, its keys checked against
  core's `SCHEMA`. The hook joins it with OpenVDM's cruise into a config TOML, writes that
  beside the outputs, and assesses from the file, so `run -c` on it repeats the run. A winch
  that can't make a config is an error when the site TOML is read, not after a transfer.
- **The site TOML's default is the install's `configs/site.toml`,** for the hook,
  `config-from-openvdm` and `site-config` (as nav-qa-processing's and sbe-qa-processing's):
  `uv sync` installs the package editable, so it finds its checkout whatever directory OpenVDM
  runs it in. Without one (a wheel), the current directory's. A missing site TOML is an error:
  without it there are no winches.
- **The hook exits 0 when the assessment ran,** whatever the checks found: a tension over the
  SWT is a finding for the wire log, not a failed hook (OpenVDM would show it as one). `run`
  still exits 1 on a failed check.
- **No cruise extent.** nav-qa-processing's hook sets OpenVDM's `cruiseExtent` from the track;
  winch data has no positions, so this hook neither sets nor reads it.
- **Reading for speed and memory, with one set of rules.** The LCI-90i reader takes the usual
  fixed-width line (every RR2403 line but about 1 in 3,000) from its bytes with numpy, a block
  at a time; every other line goes through the per-line code, which decides the same
  (`tests/test_formats.py` runs a corpus of awkward lines both ways). Records are written into
  one set of columns for the fileset, sized by its line count, so there are no per-file
  copies. This avoids per-file arrays rather than tuning the allocator: glibc keeps their freed
  memory once its mmap threshold has risen, about another copy of the cruise.
- **Idle records are dropped as they're read** (`idle.py`). Outside its deployments
  `find_casts` needs only the records near a plausible payout crossing (the padding and the
  merge gap), those of a run entered by one, and either side of each payout change; the rest,
  most of a cruise (RR2403's hydro winch: 99.4 %), are dropped as each file is settled, so a
  run holds the kept records and about a file's. Deployments, checks and the wire log are as
  with every record kept (`tests/test_idle.py`: random cruises, and a test that fails without
  each rule). The tension of every record is kept minute by minute (`WinchData.envelope`:
  each minute's minimum and maximum), for Phase 2's plot of the whole cruise; the payout's
  changes are all kept, so it plots exactly. Files out of time order are read again keeping
  every record. `WinchData.records` counts what was read; `len(data)`, what was kept.
- **Slow-haul checks are halts every ~500 m** (Table A.8.4, which defines neither a halt nor a
  slow haul). They apply only when the selected FS is in Table A.8.4: a cable halts at FS 2.0,
  and other wire rope must halt there or move to Table A.8.4. From the first record of a
  deployment's deployed stretch, paying out, at FS 2.0, to its deepest payout, a halt is the
  payout reached so far not growing a whole metre for `slow_haul_halt` (30 s), so stopping
  and a slow haul (hauling in) both count; the wire may go 500 m and `slow_haul_allowance`
  (10 %, for the "~") between halts. Whether a slow haul established the nominal and peak
  tensions is the operator's to show. The FS 1.5 halt is "Never below the lowest FS allowed".
- **Peak tension is the logged maximum**, unfiltered: Appendix A asks for the peak, and the
  tables' logging rates are there so peaks are captured. A spike filter, if a sensor needs one,
  would be a per-format QC step stated in the docs, not a silent default.

## Plan

1. **Phase 1 (this):** scaffold, Appendix A as data, config, LCI-90i and delimited readers,
   deployments, the wire log, the checks, `run`. Tests on synthetic casts.
2. **Phase 2: reports (done).** `plots.py` (the cruise's timeline from the kept records and
   the minute envelope; tension against payout with the limits; the FS histogram; each
   deployment's tension and payout against time), `theme.py`, `reports.py`, `pdf_report.py`
   (the checks first, then the winch, the deployments, the records and the figures),
   `notebook.py` (re-runs the assessment), the hook's `--theme` and `--execute-notebook` and the
   site TOML's `[theme]`, as nav-qa-processing's, and Table A.8.4's slow-haul checks.
3. **Phase 3: more formats.** MOTU, Markey and DynaCon control systems' logs, and the LCI-90i
   compared with a real unit's output; alarm conditions where a format logs them. Needs sample
   files from ships. Each format documented as nav-qa-processing's `formats/definitions/`.
4. **Phase 4: the wire record across cruises.** A.5.2.1's history log, one per tension member
   for its service life, transferring with it: what no single cruise's wire log can hold.
   - **Plain files, by NSF identifier** (`records/<NSF-ID>/`): `deployments.csv`, every
     deployment from every cruise, merged from the wire logs (`run` and the hook with
     `--wire-record DIR`; re-running a cruise replaces its rows); `events.toml`, the operator's
     dated break tests, cutbacks and spooling, lubrication, test results (TDR, resistance,
     OTDR, optical loss) and terminations with their proof tests, edited by hand (they're rare
     per cruise) and checked when read, as configs are, with an example showing every key. A
     database and a dedicated UI may come with adoption: the record is read and written only
     through one module, so it can replace the files without touching the reports or the hook.
   - **Steps, a PR each:** (1, done) the deployments ledger and a report of the deployments
     since the last break test (number, duration, deepest) and over the wire's life; (2 and 3,
     done together) the events (`configs/events.example.toml`), in the report (the length after
     the cutbacks since the last spooling, the last lubrication and termination, each kind of
     test's latest result, the events by date), and the record's break tests (every merged
     config's, kept in `tension_member.toml`, and `events.toml`'s) used by a run's checks with
     `--wire-record`, so a break test is entered once; (4) the NSF Wire Pool database export,
     once its upload format is known; (5) optionally, wear by position along the wire, tracked
     through cutbacks.
5. **Phase 5: OpenVDM hook (done, ahead of Phases 2 to 4).** `openvdm`, `config-from-openvdm`
   and `site-config` as nav-qa-processing's: run after a winch transfer, outputs into the
   cruise's extra directory, a site TOML per ship with its winches and tension members, tested
   against OpenVDM 2.17's own code (`tests/test_openvdm_contract.py`). Phase 2's reports go
   into the same extra directory (`--theme`, `--execute-notebook` as nav-qa-processing's hook
   has).
6. **Memory and speed (done in part).** RR2403's hydro winch, 4 days at 20 Hz (6.57 M
   records), outputs unchanged: 25 s and 865 MB peak before, 3.4 s and 170 MB after (macOS);
   37 s and 866 MB before, 6 s and 140 MB after (Linux). Memory now follows the largest file,
   not the cruise, and time the cruise's length: a month at 20 Hz (52 M records) about 30 s.
   Left: the hook could keep earlier days' kept records rather than re-reading them every
   run.

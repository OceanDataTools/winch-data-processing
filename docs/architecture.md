# Architecture and plan

winch-data-processing reads an oceanographic winch's raw tension, payout and speed records,
finds its deployments, writes the tension member's wire log for the cruise, and checks the
cruise against RVSS Appendix A. It follows the conventions of its siblings
[nav-qa-processing](https://github.com/OceanDataTools/nav-qa-processing) and
[sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing): uv, hatchling, a `src/`
layout, TOML configs, one entry point returning a result every writer takes, a project-wide
quantity table, and (from Phase 2) a PDF report, SVG figures and a notebook that re-runs the
assessment, and an OpenVDM hook.

What Appendix A requires, and which of it the data or the config can show, is in
[rvss-appendix-a.md](rvss-appendix-a.md).

## Pipeline

```
raw files --formats/<reader>--> Records (per file, the data's units)
          --rawdata.read_fileset--> WinchData (one series, lbf / m / m min-1, sorted, cruise days)
          --casts.find_casts--> [Cast] (deployments and their figures)
          --checks.run_checks--> [Check] (pass / fail / warn / info / n/a, by section)
          = assess.assess -> WinchResult
          --wirelog.write_outputs--> <CRUISE>_<WINCH>_wire_log.csv, <CRUISE>_<WINCH>_checks.csv
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
- `wirelog.py`: the wire log and checks CSVs.
- `cli.py`: `run`, `formats`.

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
- **Peak tension is the logged maximum**, unfiltered: Appendix A asks for the peak, and the
  tables' logging rates are there so peaks are captured. A spike filter, if a sensor needs one,
  would be a per-format QC step stated in the docs, not a silent default.

## Plan

1. **Phase 1 (this):** scaffold, Appendix A as data, config, LCI-90i and delimited readers,
   deployments, the wire log, the checks, `run`. Tests on synthetic casts.
2. **Phase 2: reports.** `plots.py` (each cast's tension and payout against time, tension
   against payout with the SWT, alarm and elastic-limit lines; the cruise's timeline; FS
   histogram), `pdf_report.py` (ReportLab + svglib, the checks first), `notebook.py` (re-runs
   the assessment), `theme.py`, as nav-qa-processing's. Table A.8.4's slow-haul checks.
3. **Phase 3: more formats.** MOTU, Markey and DynaCon control systems' logs, and the LCI-90i
   compared with a real unit's output; alarm conditions where a format logs them. Needs sample
   files from ships. Each format documented as nav-qa-processing's `formats/definitions/`.
4. **Phase 4: the wire record across cruises.** A tension member's history (deployments since
   the last break test, cutbacks, spooling, lubrication, test results) kept beside the
   per-cruise logs, and an export for the NSF Wire Pool database's upload.
5. **Phase 5: OpenVDM hook.** `openvdm` and `site-config` commands as nav-qa-processing's: run
   after a winch transfer, reports into the cruise's extra directory, a site TOML per ship with
   its winches and tension members.
6. **Then:** memory and speed for 20 Hz over long cruises (a month at 20 Hz is 52 M records), as
   nav-qa-processing's #157 did.

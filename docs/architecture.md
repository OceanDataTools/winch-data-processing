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
  one set of columns for the fileset, sized by its line count, so a run holds 32 bytes a
  record and no per-file copies; `find_casts` adds 8 a record while it runs. This avoids
  per-file arrays rather than tuning the allocator: glibc keeps their freed memory once its
  mmap threshold has risen, about another copy of the cruise.
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
5. **Phase 5: OpenVDM hook (done, ahead of Phases 2 to 4).** `openvdm`, `config-from-openvdm`
   and `site-config` as nav-qa-processing's: run after a winch transfer, outputs into the
   cruise's extra directory, a site TOML per ship with its winches and tension members, tested
   against OpenVDM 2.17's own code (`tests/test_openvdm_contract.py`). Phase 2's reports go
   into the same extra directory when they come (`--theme`, `--execute-notebook` as
   nav-qa-processing's hook has).
6. **Memory and speed (done in part).** RR2403's hydro winch, 4 days at 20 Hz (6.57 M
   records), outputs unchanged: 25 s and 865 MB peak before, 3.4 s and 361 MB after (macOS);
   37 s and 866 MB before, 6 s and 330 MB after (Linux). A month at 20 Hz (52 M records)
   would hold about 1.7 GB and peak near 2.1 GB, in about 30 s. Left: tension and speed are
   only used within deployments, so a reader that found them as it went could drop the idle
   records' (most of a cruise); and the hook could keep earlier days' columns rather than
   re-reading them every run.

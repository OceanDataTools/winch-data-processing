# winch-data-processing

Wire logs and RVSS Appendix A checks from oceanographic winch data.

[RVSS Appendix A](https://www.unols.org/document/document/research-vessel-safety-standards-rvss)
("UNOLS Rope and Cable Safe Working Standards", RVSS 11th edition, updated 25 June 2026) asks a
vessel operator to keep, for each tension member, a log with every deployment's maximum
tension, the line out at that time and the maximum payout (A.5.2.1, A.5.3.1, A.8.3.6), and to
meet the requirements of the table its selected factor of safety falls in (Tables A.8.1 to
A.8.5): tension logging rates, alarms, calibration, sheave sizes, break test intervals. This tool
reads a winch's raw tension and payout data, finds the deployments, writes the wire log, and
checks the cruise against those requirements.

Part of the [OceanDataTools](https://github.com/OceanDataTools) QA family with
[nav-qa-processing](https://github.com/OceanDataTools/nav-qa-processing) and
[sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing), and built the same way.

**Status:** Phase 1 of [docs/architecture.md](docs/architecture.md)'s plan (the readers below,
deployments, the wire log and the checks) and its [OpenVDM](#openvdm) hook. The PDF report,
figures and notebook come next.

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and git.

```bash
git clone https://github.com/OceanDataTools/winch-data-processing.git
cd winch-data-processing
uv sync
uv run pytest
```

For the OpenVDM hook, [INSTALL.md](INSTALL.md) installs it where OpenVDM's workers can run it.

## Use

```bash
cp configs/config.example.toml configs/SR2612_CTD.toml   # then fill it in
uv run winch-data-processing run data/SR2612_winch -c configs/SR2612_CTD.toml -o output
```

`run` writes `<CRUISE>_<WINCH>_wire_log.csv`, `<CRUISE>_<WINCH>_checks.csv`, the figures, as
SVGs in `<CRUISE>_<WINCH>_plots/` ([Figures](#figures)), `<CRUISE>_<WINCH>_report.pdf` ([The
PDF report](#the-pdf-report)) and `<CRUISE>_<WINCH>_report.ipynb` ([The notebook](#the-notebook)),
and prints the checks; it exits 1 when a check fails. Every output is identical from run to run
(the notebook unless it's executed).

| Command | What it does |
|---|---|
| `run DATA_DIR -c CONFIG [-o OUTPUT] [--force] [--theme THEME_TOML] [--execute-notebook]` | Read the winch's raw files, find the deployments, write the wire log, the checks, the figures, the PDF report and the notebook |
| `formats` | List the data formats |
| `openvdm TRANSFER [--site-config SITE_TOML] ...` | The OpenVDM hook: assess each of the transfer's winches for OpenVDM's current cruise ([OpenVDM](#openvdm)) |
| `config-from-openvdm TRANSFER [--site-config SITE_TOML] [-o OUTPUT_DIR]` | Write each winch's config TOML from OpenVDM's cruise, as the hook would, without assessing |
| `site-config [-o SITE_TOML] [--force]` | Write the ship's site TOML by prompting for each field |

Every command takes `-v` (INFO) or `-vv` (DEBUG).

### Data formats

| `[data] format` | Data |
|---|---|
| `lci90i` | The LCI-90i wire monitoring display's `RD` record: `02RD,time,tension [lbf],speed [m min-1],payout [m],checksum`, the checksum the sum of the bytes before it; other message types are counted, not read |
| `openrvdas_lci90i` | The same, logged by OpenRVDAS, each line led by its ISO 8601 timestamp; records split across two lines are joined. The logger's clock by default: RR2403's displays were hours to a month off |
| `delimited` | Delimited text, its time, tension, payout and speed columns named in `[data.columns]` |

The LCI-90i reader is checked against RR2403's two winches (R2R filesets 160178 and 160181,
Revelle's Markey DESH-5 and DUTW-9-11); `tests/data/` has its one cast. MOTU, Markey and DynaCon
control systems' own logs are planned and need sample files.

### The wire log

A `# keyword: value` header (the tension member, its NBL, ABL, selected FS, safe working tension
and table, the winch, the sheave train with each D/d), then a row a deployment:

| Column | Unit | |
|---|---|---|
| `cast` | 1 | Deployment number |
| `start`, `end` | | First and last record, UTC |
| `duration` | s | |
| `max_payout` | m | Maximum payout |
| `max_tension` | lbf | Maximum tension |
| `max_tension_time` | | Its time, UTC |
| `payout_at_max_tension` | m | Line out at maximum tension |
| `min_factor_of_safety` | 1 | ABL / maximum tension |
| `samples` | 1 | Tension records |
| `logging_rate` | Hz | From the median interval |
| `longest_gap` | s | Longest interval between records |

### The checks

| Check | Section | From |
|---|---|---|
| Deployments | A.5.2.1 | data |
| Tension within the safe working tension (ABL / selected FS) | A.2.19, Tables A.8.1–A.8.5 | data |
| Never below the lowest FS allowed (cable 2.0, wire rope 1.5, synthetic 5.0) | Tables A.8.3, A.8.4, A.8.3.1 | data |
| Peak tension within the elastic limit (cable FS 1.8, wire rope 1.33) | A.5.6 | data |
| Tension logging rate (3 Hz, or 20 Hz) | Tables A.8.2–A.8.5 | data |
| Gaps in the tension record | Tables A.8.2–A.8.5 | data |
| Tension alarm setting | Tables A.8.2–A.8.5 | config |
| Alarm conditions in the logged data | Tables A.8.2–A.8.5 | operator |
| Tension display refresh rate | Tables A.8.2–A.8.5 | config |
| Tension measuring system calibration (every 6 months) | Tables A.8.2–A.8.5 | config |
| Tension measuring accuracy (4 % or 3 %) | Tables A.8.2–A.8.5 | config |
| Sheaves and load-carrying rollers (D/d 40, 400 d1, groove 1.5 d) | Tables A.8.2–A.8.5 | config |
| Break test interval (24, 12 or 6 months) | Tables A.8.1–A.8.4, A.5.1.1 | config |
| Assigned breaking load (retire below 50 % of NBL) | A.5.6 | config |

What only the operator's own records can show (operator certification, deck safety, termination
plans, lubrication, cutbacks) is listed in [docs/rvss-appendix-a.md](docs/rvss-appendix-a.md).

### Figures

In `<CRUISE>_<WINCH>_plots/`, as SVG, identical from run to run:

| File | What it shows |
|---|---|
| `timeline.svg` | The cruise's payout, and its tension as each minute's lowest to highest from every record, against time; the deployments shaded and numbered, the SWT. Lines break where nothing was logged |
| `tension_and_payout.svg` | Every deployment's tension against its payout, with the limits; broken at a counter reset |
| `factor_of_safety.svg` | The deployments' records by factor of safety (ABL / tension), with the selected FS, the table's alarm point, the lowest FS allowed and the elastic limit |
| `cast_NNN.svg` | Each deployment's tension and payout against time, with the limits and its peak |

The limits are the SWT (ABL / the selected FS), the alarm setting (`[monitoring] alarm_tension`,
or the table's alarm point without one), the lowest FS the kind may be operated to, and the
elastic limit (cables and wire rope). A long series is drawn as each bin's lowest and highest
values, so no peak is lost. The look is the theme: `--theme` and the config's `[theme]`, the same
keys as nav-qa-processing's and sbe-qa-processing's.

### The PDF report

`<CRUISE>_<WINCH>_report.pdf`: the cruise and its files, a verdict line on the worst status's
color, the checks, the winch and tension member (the wire log's header: NBL, ABL, the selected
FS, the SWT, the table, break tests, sheaves), the deployments (the wire log), the records read
and skipped by reason, and the figures, embedded from the SVGs as vector drawings. The theme sets
its page size, margins, fonts, logo and footer.

### The notebook

`<CRUISE>_<WINCH>_report.ipynb` re-runs the assessment step by step: the paths (the config, the
data, the theme file, the output directory), `assess`, the checks, the wire log, the records and
every figure, then a cell that writes the reports again. Edit the config's `[thresholds]` or
tension member and re-run it to see what changes. `--execute-notebook` runs it and saves its
outputs (but the last cell's: the reports were just written); that needs nbclient and ipykernel,
in the dev group (`uv sync`), and a run takes as long again.

## Configuration

A TOML per winch and cruise; [configs/config.example.toml](configs/config.example.toml) shows
every key. Unknown keys are an error saying where they belong.

| Table | Keys |
|---|---|
| `[cruise]` | `id`; `start`, `end` (dates, optional: keep only records within them); `name`, `pi`, `location`, `vessel_name` (for the wire log's header) |
| `[provenance]` | `contact_institution`, `contact_email` (for the wire log's header) |
| `[winch]` | `name`, `manufacturer`, `model`, `record_name` (the name in the data's records) |
| `[tension_member]` | `id` (NSF identifier), `kind` (`cable`, `wire_rope`, `synthetic`), `description`, `nominal_breaking_load`, `factor_of_safety` (selected), `diameter` (d), `largest_wire_diameter` (d1), `tension_unit`, `diameter_unit` |
| `[[tension_member.break_test]]` | `date`, `load` (TBL) |
| `[[sheave]]` | `name`, `tread_diameter` (D), `groove_width` (w) |
| `[monitoring]` | `system`, `display_rate` [Hz], `last_calibration`, `accuracy` [%], `alarm_tension` |
| `[data]` | `format`, `files` (glob), `time` (`logger` or `instrument`), `tension_unit`, `payout_unit`, `speed_unit`, `delimiter`, `header_lines`, `time_format`; `[data.columns]` `time`, `tension`, `payout`, `speed` |
| `[thresholds]` | `deploy_payout` [m], `merge_gap` [s], `min_cast_duration` [s], `cast_padding` [s], `max_wire_speed` [m min-1], `max_logging_gap` [s] |
| `[theme]` | The reports' look (the PDF, figures and notebook): `page_size`, `margin`, `font`, `bold_font`, `font_scale`, `figure_font_scale`, `logo`, `organization`, `footer`, `palette` (`default`, `colorblind`); `[theme.colors]` (`primary` the tension, `secondary` the limits). The same keys as nav-qa-processing's, so one theme file (`--theme`) serves both |

## OpenVDM

On a ship running [OpenVDM](https://github.com/OceanDataTools/openvdm), the assessment runs from
OpenVDM's hooks through the cruise, as nav-qa-processing's and sbe-qa-processing's do:
- **After each transfer** of the collection system that logs the winches (e.g. OpenRVDAS), a
  `postCollectionSystemTransfer` hook assesses the cruise so far, for each winch whose files
  the transfer brought.
- **When the cruise is finalized,** a `preFinalizeCurrentCruise` hook writes the final wire
  logs on the whole cruise. OpenVDM waits for them, so they're in the finalized cruise.

**Setting it up** (the install, the extra directory, the ship's site TOML and the hooks in
`openvdm.yaml`) is in [INSTALL.md](INSTALL.md#install-for-the-openvdm-hook).

### What the hook does

- **The cruise,** from OpenVDM's API (`api/warehouse/getCruiseConfig`): its ID, name, PI,
  location and dates, and where the transfer's files and the extra directory are. An
  open-ended cruise ends today, UTC. The vessel's name and contact come from the site TOML, or
  where it leaves them blank from `openvdm.yaml`'s `vessel` block (OpenVDM 2.17).
- **The winches,** from the site TOML: a `[[winch]]` table for each, holding a config TOML's
  tables but `[cruise]` (the tension member, sheaves, monitoring, and `[winch.data]`'s format
  and files glob within the transfer's directory). The hook assesses the winches whose
  `transfer` is the hook's or blank, and with `--changed-files` only those whose files changed.
- **Writes, into the extra directory,** each winch's `<CRUISE>_<WINCH>_wire_log.csv`,
  `<CRUISE>_<WINCH>_checks.csv`, its figures (`<CRUISE>_<WINCH>_plots/`, [Figures](#figures)),
  `<CRUISE>_<WINCH>_report.pdf`, `<CRUISE>_<WINCH>_report.ipynb` and
  `<CRUISE>_<WINCH>_config.toml`, the config it made from
  OpenVDM's cruise and the site TOML, and assessed from: `run -c` on it repeats the run.
  Earlier runs' files are replaced. As root, the files are given to OpenVDM's warehouse user.
- **Adds those files to the cruise's MD5 summary,** with OpenVDM's `utils/update_md5_summary.py`
  (OpenVDM 2.16.1 or later).
- **Exits 0 whatever the checks found:** a tension over the safe working tension is a finding,
  in the checks CSV and in OpenVDM's post-hook log as a `FAIL` line, not a failed hook. A hook
  that fails (OpenVDM unreachable, a transfer that isn't there) does so in one line, which
  OpenVDM shows as the hook's message; one winch's failure doesn't stop the others.

nav-qa-processing's hook also sets OpenVDM's cruise extent; winch data has no positions, so this
one doesn't.

**A past cruise,** or any cruise without OpenVDM's API: give its `cruise_config.json` (in the
cruise's directory, where OpenVDM writes it) with `--cruise-config`, to `openvdm` or
`config-from-openvdm`.

### The site TOML

[configs/site.example.toml](configs/site.example.toml) shows every key, for Revelle's hydro and
trawl winches; `site-config` writes one by asking for each field. It lives in the install's
`configs/site.toml` (`/opt/winch-data-processing/configs/site.toml` on an OpenVDM server):
`site-config` writes it there and the hook reads it from there, whatever directory either runs
in; `-o` and `--site-config` name another.

| Table | Keys |
|---|---|
| `[vessel]` | `name` |
| `[openvdm]` | `output_extra_directory` (default `WINCH_QA`): the OpenVDM extra directory the outputs go in |
| `[provenance]` | `contact_institution`, `contact_email` |
| `[thresholds]` | As in a config TOML, for every winch; a winch's own `[winch.thresholds]` win |
| `[theme]` | As in a config TOML, for every winch's reports (paths relative to the site TOML); the hook's `--theme` file is under it |
| `[[winch]]` | `name` (names its outputs), `transfer` (optional: only that transfer's hook assesses it), `manufacturer`, `model`, `record_name`; and under it, as in a config TOML, `[winch.tension_member]`, `[[winch.tension_member.break_test]]`, `[[winch.sheave]]`, `[winch.monitoring]`, `[winch.data]`, `[winch.thresholds]` |

### Troubleshooting

OpenVDM logs the hook's output in `/var/log/openvdm/post_hooks.log`. To see it directly, run
the hook's command by hand ([INSTALL.md](INSTALL.md#5-try-it)).

| Message | What it means |
|---|---|
| `no site TOML at ...` | Neither `--site-config` nor the install's `configs/site.toml`: write it with `site-config` (INSTALL.md step 3) |
| `no collection system transfer named ...` | `TRANSFER` isn't a transfer's name in OpenVDM (Configuration > Collection System Transfers) |
| `no extra directory named 'WINCH_QA'` | Add it in OpenVDM (Configuration > Extra Directories), or set the site TOML's `[openvdm] output_extra_directory` |
| `no openvdm.yaml at ...` | OpenVDM is installed elsewhere: give `--openvdm-config` |
| `couldn't reach OpenVDM (...)` | `siteRoot` in `openvdm.yaml` isn't where OpenVDM's web server answers |
| `TRANSFER: the site config has no [[winch]] in it` | No `[[winch]]` has this `transfer`, or a blank one |
| `no new or updated winch files` | The transfer brought no file matching a winch's `[winch.data] files`; not an error |
| `... doesn't exist yet` | The transfer hasn't copied anything for this cruise yet; not an error |
| `[[winch]] NAME: ...` | That winch's tables can't make a config: the rest of the message says why, as for a config TOML |
| `the outputs aren't in OpenVDM's MD5 summary: ...` | OpenVDM's `update_md5_summary.py` is missing (OpenVDM before 2.16.1) or failed; for a past cruise, OpenVDM's warehouse doesn't have it. The outputs are written all the same |
| `0 records` | Nothing matched `[winch.data] files` within the transfer's directory, or nothing was within the cruise's dates |

## Development

```bash
uv run pytest -n auto
uv run ruff check . && uv run ruff format --check .
```

## License

MIT; see [LICENSE.md](LICENSE.md).

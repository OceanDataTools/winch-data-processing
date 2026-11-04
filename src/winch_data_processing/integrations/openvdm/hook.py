"""Runs the core assessment from an OpenVDM post-transfer hook (ported from nav-qa-processing).

OpenVDM (https://github.com/OceanDataTools/openvdm) runs ``postCollectionSystemTransfer`` hook
commands from ``openvdm.yaml`` after each collection system transfer. The cruise comes from
OpenVDM's ``api/warehouse/getCruiseConfig``: its ID, name, PI, location and dates; the warehouse
directory and MD5 summary file; and each collection system transfer's and extra directory's
destination.

What OpenVDM doesn't store comes from the per-ship site TOML (`SiteConfig`): above all the
winches, each a ``[[winch]]`` table holding what a config TOML holds but ``[cruise]`` (the
winch, its tension member and break tests, sheaves, monitoring, and ``[winch.data]``'s format
and files glob within the collection system, e.g. ``*hydro*.txt`` within ``OpenRVDAS``). A
winch's ``transfer``, when set, limits it to that collection system transfer's hook. The hook
makes each winch's config TOML from its ``[[winch]]`` table and OpenVDM's cruise (`resolve`),
writes it beside the outputs, and assesses the winch from that file, so ``run -c`` on it
repeats the run exactly.

OpenVDM 2.17 keeps the vessel's name and contact in ``openvdm.yaml``'s ``vessel`` block, which
it copies into each cruise's ``cruise_config.json``; the hook reads it from ``openvdm.yaml``
when it has that file, else from ``cruise_config.json``. The site TOML's values win; OpenVDM's
fill the fields it leaves blank, and `site-config` offers them as defaults. Its R2R IDs aren't
used.

Each run assesses the whole cruise so far, which grows with the cruise (a month at 20 Hz is 52 M
records). With ``--min-interval`` (e.g. ``6h``) the post-transfer hook skips a winch whose wire
log is newer than that (`written_within`); the same command, without it, as a
``preFinalizeCurrentCruise`` hook writes the final wire logs on the whole cruise, which OpenVDM
waits for before finalizing.

A past cruise, or any cruise without OpenVDM's API, comes from the ``cruise_config.json`` OpenVDM
writes into each cruise's directory (``--cruise-config``). `load_cruise_json` gives it the API's
shape: the file is the API's cruise config without its warehouse block, which it rebuilds from
the file's directory (the cruise's, named after it as OpenVDM names it; the warehouse is its
parent) and the MD5 summary's name the file keeps at its top level. OpenVDM takes the outputs
into the cruise's MD5 summary only if its warehouse has the cruise there (`queue_md5_update`).

nav-qa-processing's hook also sets OpenVDM's cruise extent from the ship's track; winch data has
no positions, so this one doesn't.

The OpenVDM API is read over HTTP, so this runs in its own environment, not OpenVDM's venv.
Reading ``openvdm.yaml`` needs pyyaml, the ``openvdm`` extra.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tomllib
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from winch_data_processing.config import (
    ARRAYS,
    SCHEMA,
    Config,
    ConfigError,
    check_keys,
    config_from_dict,
    dumps_toml,
    toml_value,
)
from winch_data_processing.network import reaching
from winch_data_processing.rvss import StandardError
from winch_data_processing.theme import ThemeError, theme_table
from winch_data_processing.wirelog import safe_name

logger = logging.getLogger(__name__)

DEFAULT_OPENVDM_CONFIG = Path("/opt/openvdm/server/etc/openvdm.yaml")


def install_site_config(module: Path = Path(__file__)) -> Path:
    """The site TOML's default path: configs/site.toml in the checkout this package runs from
    (`uv sync` installs it editable, so the hook finds /opt/winch-data-processing/configs/site.toml
    whatever directory OpenVDM runs it in), else in the current directory
    """
    here = module.resolve()  # <checkout>/src/winch_data_processing/integrations/openvdm/hook.py
    checkout = here.parents[4]
    if here.parents[3].name == "src" and (checkout / "pyproject.toml").is_file():
        return checkout / "configs" / "site.toml"
    return Path("configs/site.toml")


DEFAULT_SITE_CONFIG = install_site_config()
DEFAULT_EXTRA_DIRECTORY = "WINCH_QA"
API_TIMEOUT = 30  # [s]
MD5_UPDATE_TIMEOUT = 120  # [s] to queue the job, not to hash the files


class OpenVDMError(RuntimeError):
    """OpenVDM's or the site's configuration doesn't support running the assessment (e.g. a
    missing directory)
    """


# A [[winch]] table's own tables: a config TOML's, under the winch
WINCH_TABLES = ("tension_member", "sheave", "monitoring", "data", "thresholds")


@dataclass
class SiteWinch:
    """A winch the hook assesses: a [[winch]] table of the site TOML"""

    name: str
    # The config TOML's tables for it but [cruise]: [winch] (name, manufacturer, model,
    # record_name), [tension_member], [[sheave]], [monitoring], [data], [thresholds]; in the
    # site TOML's units
    tables: dict = field(default_factory=dict)
    transfer: str = ""  # the collection system transfer it's in; any, when blank

    @property
    def files(self) -> str:
        """Its [winch.data] files glob, within the collection system's directory"""
        return self.tables.get("data", {}).get("files", "*")


@dataclass
class SiteConfig:
    """Per-ship settings OpenVDM doesn't store, from the site TOML. From OpenVDM 2.17, the
    vessel name and contact are also in openvdm.yaml, which fills these where they're blank.
    `configs/site.example.toml` shows every key::

    [vessel]
    name = "Roger Revelle"

    [openvdm]
    output_extra_directory = "WINCH_QA"

    [provenance]
    contact_institution = "Scripps Institution of Oceanography"
    contact_email = "ship-tech@example.org"

    [thresholds]              # every winch's, as in a config TOML; a winch's own win

    [theme]                   # every winch's reports', as in a config TOML; paths relative to
                              # this file

    [[winch]]                 # one a winch: a config TOML's tables but [cruise], under it
    name = "hydro"
    transfer = "OpenRVDAS"    # optional: only that transfer's hook assesses it
    [winch.tension_member]
    ...
    [winch.data]
    format = "openrvdas_lci90i"
    files = "*hydro*.txt"     # within the collection system transfer's directory
    """

    vessel_name: str = ""
    winches: list[SiteWinch] = field(default_factory=list)
    output_extra_directory: str = DEFAULT_EXTRA_DIRECTORY
    contact_institution: str = ""
    contact_email: str = ""
    thresholds: dict = field(default_factory=dict)  # [thresholds], as in a config TOML
    theme: dict = field(default_factory=dict)  # [theme], checked, its paths absolute

    def winches_for(self, transfer: str) -> list[SiteWinch]:
        """The winches a transfer's hook assesses: those naming it, and those naming none"""
        return [winch for winch in self.winches if winch.transfer in ("", transfer)]


# The site TOML's keys: any other is an error naming where it belongs. A winch's tables are the
# config TOML's (SCHEMA), so a key added there is accepted here too
SITE_KEYS = {
    "vessel": {"name": None},
    "openvdm": {"output_extra_directory": None},
    "provenance": SCHEMA["provenance"],
    "thresholds": SCHEMA["thresholds"],
    "theme": SCHEMA["theme"],
    "winch": {
        **SCHEMA["winch"],
        "transfer": None,
        **{table: SCHEMA[table] for table in WINCH_TABLES},
    },
}
SITE_ARRAYS = {("winch",), *(("winch", *path) for path in ARRAYS)}


def load_site_config(path: Path | str | None) -> SiteConfig:
    if path is None:
        return SiteConfig()
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: {error}") from None
    if "cruise" in data:
        # A site TOML describes the ship, so a cruise table in it is a mistake worth explaining
        raise ConfigError(
            f"{path}: a site TOML has no [cruise]: the hook gets each cruise's ID, name, chief "
            "scientist (cruisePI), location and dates from OpenVDM; the vessel's name is "
            "[vessel] name"
        )
    if isinstance(data.get("winch"), dict):
        raise ConfigError(f"{path}: winch is an array of tables: [[winch]], one a winch")
    check_keys(data, SITE_KEYS, str(path), arrays=SITE_ARRAYS)
    openvdm, provenance = data.get("openvdm", {}), data.get("provenance", {})
    site = SiteConfig(
        vessel_name=data.get("vessel", {}).get("name", ""),
        output_extra_directory=openvdm.get("output_extra_directory", DEFAULT_EXTRA_DIRECTORY),
        contact_institution=provenance.get("contact_institution", ""),
        contact_email=provenance.get("contact_email", ""),
        thresholds=data.get("thresholds", {}),
    )
    try:
        site.theme = theme_table(data.get("theme", {}), path.parent, str(path))
    except ThemeError as error:
        raise ConfigError(str(error)) from None
    names = set()
    for number, table in enumerate(data.get("winch", []), 1):
        winch = site_winch(table)
        if not winch.name:
            raise ConfigError(f"{path}: [[winch]] {number} needs name")
        if safe_name(winch.name) in names:
            raise ConfigError(f"{path}: two [[winch]] tables are named {winch.name!r}")
        names.add(safe_name(winch.name))
        # A winch that can't make a config is an error now, not at sea after a transfer
        winch_config(site, winch, {"id": "SITE"}, f"{path}: [[winch]] {winch.name}")
        site.winches.append(winch)
    return site


def site_winch(table: dict) -> SiteWinch:
    """A [[winch]] table as a SiteWinch: its transfer, and the config TOML's tables"""
    winch = {k: v for k, v in table.items() if k not in WINCH_TABLES and k != "transfer"}
    tables = {"winch": winch, **{t: table[t] for t in WINCH_TABLES if t in table}}
    return SiteWinch(str(winch.get("name", "")), tables, str(table.get("transfer", "")))


def winch_tables(site: SiteConfig, winch: SiteWinch) -> dict:
    """The winch's config TOML tables but [cruise], the site's [thresholds] under its own, and
    the site's [theme]
    """
    tables = dict(winch.tables)
    thresholds = {**site.thresholds, **winch.tables.get("thresholds", {})}
    tables.pop("thresholds", None)
    if thresholds:
        tables["thresholds"] = thresholds
    if site.theme:
        tables["theme"] = site.theme
    return tables


def winch_config(
    site: SiteConfig, winch: SiteWinch, cruise: dict, where: str, provenance: dict | None = None
) -> tuple[Config, dict]:
    """The winch's Config for a cruise ([cruise] and [provenance] tables), and the config TOML's
    tables it came from
    """
    tables = {"cruise": cruise}
    if provenance:
        tables["provenance"] = provenance
    tables |= winch_tables(site, winch)
    try:
        return config_from_dict(tables, None, where), tables
    except (ConfigError, StandardError) as error:
        message = str(error)
        raise ConfigError(
            message if message.startswith(where) else f"{where}: {message}"
        ) from None


def site_config_to_toml(site: SiteConfig) -> str:
    """The site config as TOML that load_site_config reads back. Blank fields are written
    commented out, so the file shows everything that can be set
    """

    def entry(key: str, value, comment: str = "") -> str:
        line = f"{key} = {toml_value(value)}" if value else f'# {key} = ""'
        return f"{line:<40} # {comment}" if comment else line

    lines = [
        "# Per-ship settings for the OpenVDM hook (winch-data-processing openvdm --site-config",
        "# ...). OpenVDM supplies the cruise ID, name, PI, location and dates. From OpenVDM 2.17,",
        "# openvdm.yaml's vessel block has the vessel name and contact too; these win, and",
        "# openvdm.yaml fills any left blank.",
        "",
        "[vessel]",
        entry("name", site.vessel_name),
        "",
        "[openvdm]",
        entry("output_extra_directory", site.output_extra_directory, "where outputs go"),
        "",
        "[provenance]",
        entry("contact_institution", site.contact_institution),
        entry("contact_email", site.contact_email),
        "",
        "# Every winch's thresholds, as in a config TOML; a winch's [winch.thresholds] win",
    ]
    if site.thresholds:
        lines += [dumps_toml({"thresholds": site.thresholds}).rstrip("\n")]
    else:
        lines += ["# [thresholds]", "# deploy_payout = 10.0"]
    lines += [
        "",
        "# Every winch's reports' theme, as in a config TOML (paths relative to this file)",
    ]
    if site.theme:
        lines += [dumps_toml({"theme": site.theme}).rstrip("\n")]
    else:
        lines += ["# [theme]", '# page_size = "A4"', '# palette = "colorblind"']
    lines += [
        "",
        "# The winches: a [[winch]] table for each, holding a config TOML's tables but [cruise]",
        "# (configs/config.example.toml), each under the winch: [winch.tension_member],",
        "# [[winch.tension_member.break_test]], [[winch.sheave]], [winch.monitoring],",
        "# [winch.data] (its files glob within the collection system transfer's directory) and",
        "# [winch.thresholds]. transfer, when set, limits a winch to that transfer's hook.",
    ]
    for winch in site.winches:
        table = {**winch.tables["winch"]}
        if winch.transfer:
            table["transfer"] = winch.transfer
        table |= {k: v for k, v in winch.tables.items() if k != "winch"}
        lines += ["", dumps_toml({"winch": [table]}).rstrip("\n")]
        if "sheave" not in winch.tables:
            lines += ["", "# Each sheave and load-carrying roller, winch to water (A.8.2):"]
            lines += ["# [[winch.sheave]]", '# name = ""', "# tread_diameter = 0.0"]
    return "\n".join(lines) + "\n"


def load_openvdm_yaml(openvdm_config: Path | str) -> dict:
    try:
        import yaml
    except ImportError:
        raise OpenVDMError(
            "reading openvdm.yaml needs pyyaml, the openvdm extra: uv sync --extra openvdm, or "
            "pip install 'winch-data-processing[openvdm]'"
        ) from None
    try:
        with open(openvdm_config) as file:
            return yaml.safe_load(file) or {}
    except FileNotFoundError:
        # e.g. OpenVDM installed outside /opt (its installer's INSTALL_ROOT)
        raise OpenVDMError(
            f"no openvdm.yaml at {openvdm_config}: give its path with --openvdm-config, or a "
            "cruise's cruise_config.json with --cruise-config"
        ) from None


def site_root_from_config(openvdm_config: Path | str | dict) -> str:
    """OpenVDM's web root (``siteRoot`` in openvdm.yaml, or its loaded contents), ending in a
    slash
    """
    data = (
        openvdm_config if isinstance(openvdm_config, dict) else load_openvdm_yaml(openvdm_config)
    )
    if not data.get("siteRoot"):
        raise OpenVDMError("openvdm.yaml has no siteRoot, OpenVDM's web address")
    site_root = str(data["siteRoot"])
    return site_root if site_root.endswith("/") else site_root + "/"


# OpenVDM 2.17's vessel block: the SiteConfig field each of its settings fills. Its r2r IDs
# aren't used
VESSEL_SETTINGS = {
    "vessel_name": ("name",),
    "contact_institution": ("contact", "institution"),
    "contact_email": ("contact", "email"),
}


@dataclass
class OpenVDMVessel:
    """The vessel settings OpenVDM has, by SiteConfig field, and where they came from"""

    settings: dict[str, str] = field(default_factory=dict)
    source: str = "none"  # "openvdm.yaml", the cruise config file's name, or "none"


def vessel_settings(block, source: str) -> OpenVDMVessel:
    """The set fields of a ``vessel`` block. Empty and missing values are unset"""
    settings = {}
    for name, path in VESSEL_SETTINGS.items():
        value = block
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None and not isinstance(value, dict) and str(value).strip():
            settings[name] = str(value).strip()
    return OpenVDMVessel(settings, source)


# OpenVDM's name for each cruise's config file (CRUISE_CONFIG_FN), when its API doesn't say
CRUISE_CONFIG_FN = "cruise_config.json"


def cruise_vessel(cruise_dir: Path, config_filename: str) -> OpenVDMVessel:
    """The vessel block saved in the cruise's cruise_config.json, if any"""
    path = cruise_dir / config_filename
    try:
        block = json.loads(path.read_text()).get("vessel")
    except FileNotFoundError:
        return OpenVDMVessel()
    except (OSError, ValueError, AttributeError) as error:
        logger.warning("can't read the vessel settings from %s: %s", path, error)
        return OpenVDMVessel()
    return vessel_settings(block, config_filename) if block else OpenVDMVessel()


def load_cruise_json(path: Path | str) -> dict:
    """A cruise's config from the cruise_config.json OpenVDM writes into its directory: any
    cruise, past or current, without OpenVDM's API (--cruise-config)
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise OpenVDMError(f"can't read the cruise config {path}: {error}") from None
    if not isinstance(data, dict) or not data.get("cruiseID"):
        raise OpenVDMError(f"{path} isn't an OpenVDM cruise config: it has no cruiseID")
    cruise_dir = path.absolute().parent
    if cruise_dir.name != data["cruiseID"]:
        raise OpenVDMError(
            f"{cruise_dir} is named {cruise_dir.name!r}, but its cruise is "
            f"{data['cruiseID']!r}: OpenVDM names a cruise's directory after it"
        )
    # The warehouse block the API has and the file doesn't: the cruise's directory is in it
    warehouse = {
        "shipboardDataWarehouseBaseDir": str(cruise_dir.parent),
        "shipboardDataWarehouseUsername": _owner_of(cruise_dir),
        "md5SummaryFn": data.get("md5SummaryFn", ""),
        "cruiseConfigFn": path.name,
    }
    return {**data, "warehouseConfig": warehouse}


def _owner_of(path: Path) -> str:
    """The user owning path, to give the outputs to; '' where that can't be told (Windows)"""
    try:
        import pwd

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (ImportError, KeyError, OSError):
        return ""


def fetch_cruise_config(site_root: str) -> dict:
    """The current cruise's configuration from OpenVDM's API"""
    url = f"{site_root}api/warehouse/getCruiseConfig"
    with reaching("OpenVDM", url), urllib.request.urlopen(url, timeout=API_TIMEOUT) as response:
        body = response.read()
    return json.loads(body)


def parse_openvdm_date(text: str | None) -> date | None:
    """OpenVDM dates are 'YYYY/MM/DD HH:MM' (or just 'YYYY/MM/DD')"""
    if not text:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def utc_today() -> date:
    """Today's UTC date: record times are UTC, so an open-ended cruise ends on this day"""
    return datetime.now(UTC).date()


def _translate(path: str, cruise_id: str) -> str:
    return path.replace("{cruiseID}", cruise_id)


@dataclass
class HookRun:
    config: Config
    tables: dict  # the config TOML's tables the config came from, for its snapshot
    transfer: str
    cruise_dir: Path
    fileset_dir: Path
    output_dir: Path
    owner: str  # the warehouse user, for file ownership
    vessel_source: str  # where OpenVDM's vessel block came from: "openvdm.yaml", the cruise
    # config file's name (cruise_config.json by default), or "none"
    # What the config is missing, or had to assume
    notes: list[str] = field(default_factory=list)


def resolve(
    cruise_config: dict,
    site: SiteConfig,
    transfer_name: str,
    winch: SiteWinch,
    vessel: OpenVDMVessel | None = None,
) -> HookRun:
    """The config and directories for one of the site's winches in a collection system
    transfer. vessel is openvdm.yaml's vessel block; without it, the one in the cruise's
    cruise_config.json is used. cruise_config is the API's, or a cruise's cruise_config.json as
    `load_cruise_json` gives it
    """
    warehouse = cruise_config["warehouseConfig"]
    cruise_id = cruise_config["cruiseID"]
    if not cruise_id:
        raise OpenVDMError("OpenVDM has no current cruise")
    cruise_dir = Path(warehouse["shipboardDataWarehouseBaseDir"]) / cruise_id

    transfers = {t["name"]: t for t in cruise_config.get("collectionSystemTransfersConfig", [])}
    if transfer_name not in transfers:
        raise OpenVDMError(f"no collection system transfer named {transfer_name!r}")
    fileset_dir = cruise_dir / _translate(transfers[transfer_name]["destDir"], cruise_id)

    extras = {d["name"]: d for d in cruise_config.get("extraDirectoriesConfig", [])}
    output = extras.get(site.output_extra_directory)
    if output is None:
        raise OpenVDMError(
            f"no extra directory named {site.output_extra_directory!r}: add it in OpenVDM "
            "(Configuration > Extra Directories) or set [openvdm] output_extra_directory"
        )
    output_dir = cruise_dir / _translate(output["destDir"], cruise_id)

    notes = []
    depart = parse_openvdm_date(cruise_config.get("cruiseStartDate"))
    arrive = parse_openvdm_date(cruise_config.get("cruiseEndDate"))
    if depart is None:
        notes.append("OpenVDM's cruise has no start date, so every record is read")
        arrive = None
    elif arrive is None:
        # an open-ended cruise is still under way
        arrive = utc_today()
        notes.append(
            f"OpenVDM's cruise has no end date; it ends today, UTC ({arrive}), so records "
            "after today are left out"
        )
    if vessel is None:
        config_filename = warehouse.get("cruiseConfigFn") or CRUISE_CONFIG_FN
        vessel = cruise_vessel(cruise_dir, config_filename)
    # The site config's vessel settings win; OpenVDM's fill those it leaves blank
    ship = {}
    for name in VESSEL_SETTINGS:
        theirs, ours = vessel.settings.get(name, ""), getattr(site, name)
        ship[name] = ours or theirs
        if theirs and ours and theirs != ours:
            notes.append(
                f"{name} is {ours!r} in the site config and {theirs!r} in OpenVDM's "
                f"{vessel.source}; using the site config's"
            )
    cruise = {
        "id": cruise_id,
        "name": cruise_config.get("cruiseName") or "",
        "pi": cruise_config.get("cruisePI") or "",
        "location": cruise_config.get("cruiseLocation") or "",
        "vessel_name": ship["vessel_name"],
        "start": depart,
        "end": arrive,
    }
    provenance = {k: ship[k] for k in ("contact_institution", "contact_email")}
    config, tables = winch_config(
        site,
        winch,
        {k: v for k, v in cruise.items() if v},
        f"[[winch]] {winch.name}",
        {k: v for k, v in provenance.items() if v},
    )
    return HookRun(
        config=config,
        tables=tables,
        transfer=transfer_name,
        cruise_dir=cruise_dir,
        fileset_dir=fileset_dir,
        output_dir=output_dir,
        owner=warehouse.get("shipboardDataWarehouseUsername") or "",
        vessel_source=vessel.source,
        notes=notes,
    )


def cruise_toml(run: HookRun, source: str) -> str:
    """The resolved config TOML, headed by where it came from (OpenVDM's API or a cruise's
    cruise_config.json) and what it's missing
    """
    header = [f"# Resolved from {source} for {run.transfer}"]
    header += [f"# Note: {note}" for note in run.notes]
    return "\n".join(header) + "\n\n" + dumps_toml(run.tables)


def _glob_pattern(files: str) -> re.Pattern:
    """A regex finding paths matched by the glob `files` anywhere in a text"""
    parts = []
    i = 0
    while i < len(files):
        if files.startswith("**/", i):
            parts.append("(?:[^ ]*/)?")
            i += 3
        elif files[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif files[i] == "?":
            parts.append("[^/]")
            i += 1
        elif files[i] == "[" and "]" in files[i + 1 :]:
            end = files.index("]", i + 1)
            body = files[i + 1 : end]
            parts.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
        else:
            parts.append(re.escape(files[i]))
            i += 1
    return re.compile("".join(parts))


def has_winch_changes(changed: list[str], files: str) -> bool:
    """Whether OpenVDM's {newFiles}/{updatedFiles} include the winch's files: paths matching
    its `[winch.data] files` glob. File names arrive space-joined (and may themselves contain
    spaces), so the glob is searched for in the text; a name can match when it shouldn't (a
    file in a subdirectory for `*`), never the other way round
    """
    pattern = _glob_pattern(files)
    return any(text.strip() and pattern.search(text) for text in changed)


# --min-interval's units [s]
DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])")


def duration_seconds(text: str) -> float:
    """A duration such as 90s, 30m, 6h or 1d, in seconds; ValueError for anything else"""
    match = _DURATION.fullmatch(text.strip().lower())
    if match is None:
        raise ValueError(f"{text!r} isn't a duration: a number then s, m, h or d, e.g. 6h")
    return float(match.group(1)) * DURATION_UNITS[match.group(2)]


def written_within(path: Path, seconds: float, now: datetime | None = None) -> datetime | None:
    """When `path` was last written, if it exists and that's within `seconds` of `now`; else
    None. The hook's --min-interval: a long cruise's outputs are rebuilt at most that often
    """
    if not path.exists():
        return None
    written = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    age = ((now or datetime.now(UTC)) - written).total_seconds()
    return written if age < seconds else None


def openvdm_install_dir(openvdm_config: Path | str) -> Path:
    """OpenVDM's install directory, from its <install>/server/etc/openvdm.yaml"""
    return Path(openvdm_config).absolute().parents[2]


def queue_md5_update(install_dir: Path, run: HookRun, written: list[Path]) -> str | None:
    """Queues an MD5 summary update for the files the hook wrote, with OpenVDM's
    utils/update_md5_summary.py (OpenVDM 2.16.1). OpenVDM only hashes the files a transfer or
    data dashboard job reports, so without this the outputs stay out of the summary. The files
    are given by their full paths: for the current cruise those are OpenVDM's own, and for a
    cruise from its cruise_config.json OpenVDM refuses them unless its warehouse has the cruise
    there. Returns what went wrong, or None
    """
    python = install_dir / "venv" / "bin" / "python"
    script = install_dir / "utils" / "update_md5_summary.py"
    if not (python.is_file() and script.is_file()):
        return f"no {script} with {python} (it needs OpenVDM 2.16.1 or later)"
    files = sorted(
        {
            f
            for path in written
            for f in ([path] if path.is_file() else path.rglob("*"))
            if f.is_file()
        }
    )
    if not all(f.is_relative_to(run.cruise_dir) for f in files):
        return f"the outputs in {run.output_dir} are outside the cruise directory {run.cruise_dir}"
    paths = [str(f.absolute()) for f in files]
    # --cruise-id: the script's option in every OpenVDM release that has it (2.16.1-2.16.3)
    command = [str(python), str(script), "--cruise-id", run.config.cruise_id, *paths]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=MD5_UPDATE_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"couldn't run {script}: {error}"
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip().splitlines()
        return f"{script} exited {completed.returncode}: {output[-1] if output else 'no output'}"
    return None


def chown_tree(path: Path, owner: str) -> None:
    """Give the outputs to the warehouse user, as OpenVDM does, when running as root"""
    if not owner or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    for item in [path, *path.rglob("*")]:
        try:
            shutil.chown(item, user=owner, group=owner)
        except (LookupError, OSError) as error:
            logger.warning("could not chown %s to %s: %s", item, owner, error)
            return

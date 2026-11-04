"""The OpenVDM commands: `openvdm` (the post-transfer hook), `config-from-openvdm` and
`site-config`, named as in nav-qa-processing and sbe-qa-processing. Core's cli.py adds them with
`add_commands`. Installed `openvdm.yaml` files call the hook, so its arguments keep their names;
in `openvdm` mode every error is one line, because OpenVDM shows it (`every_error_one_line`).

The hook exits 0 when the assessment ran, whatever the checks found: a tension over the safe
working tension is a finding for the wire log and the checks CSV, not a failed hook. Its FAIL
lines are in OpenVDM's post-hook log.
"""

import argparse
import logging
import sys
from pathlib import Path

from winch_data_processing.clitools import (
    STDOUT,
    print_written,
    refuse_overwrite,
    write_config_toml,
)

# Stdlib only: pyyaml (the openvdm extra) is imported when openvdm.yaml is read
from winch_data_processing.integrations.openvdm.hook import (
    DEFAULT_OPENVDM_CONFIG,
    DEFAULT_SITE_CONFIG,
    OpenVDMError,
)

logger = logging.getLogger(__name__)
# Errors that are a one-line message; in `openvdm` mode every error is one line anyway
EXPECTED_ERRORS: tuple[type[Exception], ...] = (OpenVDMError,)


def _site_toml(args: argparse.Namespace) -> Path:
    """--site-config, else the install's configs/site.toml; it must exist"""
    from winch_data_processing.integrations.openvdm import hook as openvdm

    path = args.site_config or openvdm.DEFAULT_SITE_CONFIG
    if not path.is_file():
        fix = (
            "check --site-config"
            if args.site_config
            else "write it with `winch-data-processing site-config`, or give --site-config"
        )
        raise OpenVDMError(f"no site TOML at {path}; {fix}")
    return path


def _resolve_openvdm(args: argparse.Namespace, site=None, winches=None):
    """The cruise's HookRuns, one per winch (the site's winches for the transfer, unless given):
    the current cruise from OpenVDM's API and the site config, or with --cruise-config any
    cruise from its cruise_config.json. With them OpenVDM's web root ('' without the API)
    """
    from winch_data_processing.integrations.openvdm import hook as openvdm

    if args.cruise_config:
        # Any cruise, past or current, without the API; the vessel is the file's
        cruise_config = openvdm.load_cruise_json(args.cruise_config)
        site_root, vessel = "", None
    else:
        settings = openvdm.load_openvdm_yaml(args.openvdm_config)
        site_root = openvdm.site_root_from_config(settings)
        vessel = openvdm.vessel_settings(settings.get("vessel"), "openvdm.yaml")
        cruise_config = openvdm.fetch_cruise_config(site_root)
    site = site or openvdm.load_site_config(_site_toml(args))
    winches = site.winches_for(args.transfer) if winches is None else winches
    runs = [
        openvdm.resolve(cruise_config, site, args.transfer, winch, vessel) for winch in winches
    ]
    return runs, site_root


def _source(args: argparse.Namespace, site_root: str) -> str:
    """Where the cruise came from, for the config snapshot's header"""
    return str(args.cruise_config) if args.cruise_config else f"OpenVDM ({site_root})"


def _duration(text: str) -> str:
    """--min-interval's value, checked as the arguments are parsed"""
    from winch_data_processing.integrations.openvdm.hook import duration_seconds

    try:
        duration_seconds(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return text


def _openvdm(args: argparse.Namespace) -> int:
    from winch_data_processing.integrations.openvdm import hook as openvdm
    from winch_data_processing.theme import resolve_theme

    site = openvdm.load_site_config(_site_toml(args))
    resolve_theme(args.theme)  # a theme file that can't be used fails before any work
    winches = site.winches_for(args.transfer)
    if not winches:
        print(f"{args.transfer}: the site config has no [[winch]] in it; nothing to do")
        return 0
    if args.changed_files is not None:
        winches = [w for w in winches if openvdm.has_winch_changes(args.changed_files, w.files)]
        if not winches:
            print(f"{args.transfer}: no new or updated winch files; nothing to do")
            return 0
    runs, site_root = _resolve_openvdm(args, site, winches)
    # One winch's failure doesn't stop the others; OpenVDM shows them all, on one line
    failures = []
    for run in runs:
        try:
            _openvdm_winch(args, run, site_root)
        except Exception as error:
            if len(runs) == 1:
                raise
            logger.debug("winch %s failed", run.config.winch_name, exc_info=True)
            failures.append(f"{run.config.winch_name}: {type(error).__name__}: {error}")
    if failures:
        raise OpenVDMError(f"{len(failures)} of {len(runs)} winches failed: {'; '.join(failures)}")
    return 0


def _openvdm_winch(args: argparse.Namespace, run, site_root: str) -> None:
    """Assesses one winch and writes its outputs, unless it has no files yet or its outputs are
    newer than --min-interval
    """
    from winch_data_processing.assess import assess
    from winch_data_processing.cli import summary
    from winch_data_processing.config import load_config
    from winch_data_processing.integrations.openvdm import hook as openvdm
    from winch_data_processing.reports import report_paths, write_reports
    from winch_data_processing.wirelog import stem

    name = stem(run.config)
    if not run.fileset_dir.is_dir():
        print(f"{run.fileset_dir} doesn't exist yet; nothing to do")
        return
    if args.min_interval:
        wire_log = report_paths(run.config, run.output_dir)["wire log"]
        written = openvdm.written_within(wire_log, openvdm.duration_seconds(args.min_interval))
        if written is not None:
            print(
                f"{name}: its wire log, from {written:%Y-%m-%d %H:%M} UTC, is newer than "
                f"--min-interval {args.min_interval}; nothing to do"
            )
            return

    print(
        f"{name}: {run.fileset_dir} -> {run.output_dir} (vessel settings from {run.vessel_source})"
    )
    for note in run.notes:
        print(f"  note: {note}")
    run.output_dir.mkdir(parents=True, exist_ok=True)
    # Snapshot what OpenVDM and the site TOML supplied, and assess from it, so `run -c` on it
    # repeats exactly this run
    snapshot = run.output_dir / f"{name}_config.toml"
    snapshot.write_text(openvdm.cruise_toml(run, _source(args, site_root)))
    result = assess(run.fileset_dir, load_config(snapshot))
    for error in result.data.errors:
        print(f"  unread: {error}")
    reports = write_reports(
        result, run.output_dir, theme_file=args.theme, execute_notebook=args.execute_notebook
    )
    written = [snapshot, *reports]
    openvdm.chown_tree(run.output_dir, run.owner)
    print_written(written)
    if args.wire_record:
        from winch_data_processing.cli import merge_wire_record

        merge_wire_record(args.wire_record, result)
    print("\n".join(summary(result)))
    _queue_md5_update(args, run, written)


def _queue_md5_update(args: argparse.Namespace, run, written: list[Path]) -> None:
    """Adds the outputs to OpenVDM's MD5 summary. The assessment itself succeeded, so a failure
    here is a warning, not a failed hook
    """
    from winch_data_processing.integrations.openvdm import hook as openvdm

    install = openvdm.openvdm_install_dir(args.openvdm_config)
    problem = openvdm.queue_md5_update(install, run, written)
    if problem:
        logger.warning("the outputs aren't in OpenVDM's MD5 summary: %s", problem)
    else:
        print(f"queued an MD5 summary update for the outputs in {run.output_dir}")


def _config_from_openvdm(args: argparse.Namespace) -> int:
    from winch_data_processing.integrations.openvdm import hook as openvdm
    from winch_data_processing.wirelog import stem

    runs, site_root = _resolve_openvdm(args)
    if not runs:
        raise OpenVDMError(f"the site config has no [[winch]] in {args.transfer}")
    if str(args.output) == STDOUT and len(runs) > 1:
        names = ", ".join(run.config.winch_name for run in runs)
        raise OpenVDMError(f"{args.transfer} has several winches ({names}): give -o OUTPUT_DIR")
    for run in runs:
        print(f"{stem(run.config)}: vessel settings from {run.vessel_source}", file=sys.stderr)
        for note in run.notes:
            print(f"  note: {note}", file=sys.stderr)
        text = openvdm.cruise_toml(run, _source(args, site_root))
        write_config_toml(text, stem(run.config), args)
    return 0


def _site_config(args: argparse.Namespace) -> int:
    from winch_data_processing.integrations.openvdm import hook as openvdm
    from winch_data_processing.integrations.openvdm.hook import (
        SiteConfig,
        load_openvdm_yaml,
        load_site_config,
        site_config_to_toml,
        vessel_settings,
    )
    from winch_data_processing.integrations.openvdm.site_setup import prompt_site_config

    args.output = args.output or openvdm.DEFAULT_SITE_CONFIG
    refuse_overwrite([args.output], args.force)
    # OpenVDM 2.17's vessel settings are the defaults where the site config has none
    openvdm_yaml = args.openvdm_config
    if openvdm_yaml is not None and not openvdm_yaml.exists():
        raise OpenVDMError(f"no openvdm.yaml at {openvdm_yaml}; check --openvdm-config")
    if openvdm_yaml is None and DEFAULT_OPENVDM_CONFIG.exists():
        openvdm_yaml = DEFAULT_OPENVDM_CONFIG
    elif openvdm_yaml is None:
        # Not an error: the site TOML may be written away from the OpenVDM server
        print(
            f"No openvdm.yaml at {DEFAULT_OPENVDM_CONFIG}; asking for every field. If OpenVDM "
            "2.17 is installed elsewhere, use --openvdm-config so its vessel settings are "
            "offered.\n",
            file=sys.stderr,
        )
    from_openvdm = {}
    if openvdm_yaml is not None:
        block = load_openvdm_yaml(openvdm_yaml).get("vessel")
        from_openvdm = vessel_settings(block, "").settings
        if not block:
            # e.g. an openvdm.yaml carried over from OpenVDM 2.16
            print(
                f"{openvdm_yaml} has no vessel block (OpenVDM 2.17 adds one: see its "
                "openvdm.yaml.dist); the vessel fields' defaults are the site TOML's, or blank.\n",
                file=sys.stderr,
            )
    # Re-running over an existing file starts from its values
    existing = str(args.output) != STDOUT and args.output.exists()
    defaults = load_site_config(args.output) if existing else SiteConfig()
    text = site_config_to_toml(prompt_site_config(defaults, from_openvdm=from_openvdm))
    if str(args.output) == STDOUT:
        print(text, end="")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


def _add_openvdm_arguments(parser: argparse.ArgumentParser) -> None:
    """Where to find OpenVDM and the site config, shared by the hook and config-from-openvdm"""
    parser.add_argument(
        "transfer",
        metavar="TRANSFER",
        help="collection system transfer name, e.g. {collectionSystemTransferName}",
    )
    parser.add_argument(
        "--openvdm-config",
        metavar="OPENVDM_YAML",
        type=Path,
        default=DEFAULT_OPENVDM_CONFIG,
        help=(
            "openvdm.yaml, for OpenVDM's siteRoot and vessel settings (default %(default)s); "
            "with --cruise-config, only where OpenVDM is, for the MD5 summary"
        ),
    )
    parser.add_argument(
        "--cruise-config",
        metavar="CRUISE_CONFIG_JSON",
        type=Path,
        help=(
            "a cruise's cruise_config.json, in its directory: that cruise, past or current, "
            "without OpenVDM's API. Its directory is the cruise's; its vessel block is used"
        ),
    )
    parser.add_argument(
        "--site-config",
        metavar="SITE_TOML",
        type=Path,
        help=(
            "per-ship TOML: the [[winch]] tables (tension member, sheaves, data), vessel, ... "
            f"(default {DEFAULT_SITE_CONFIG}, the install's)"
        ),
    )


def add_commands(commands) -> None:
    hook = commands.add_parser(
        "openvdm",
        help="assess the current cruise's winches from an OpenVDM post-transfer hook",
        description=(
            "Reads the current cruise from OpenVDM's API, assesses each of the collection "
            "system transfer's winches ([[winch]] in the site config), and writes their wire "
            "logs and checks into an OpenVDM extra directory, replacing earlier ones."
        ),
    )
    _add_openvdm_arguments(hook)
    hook.add_argument(
        "--changed-files",
        action="append",
        metavar="FILES",
        help=(
            "OpenVDM's {newFiles}/{updatedFiles}; repeatable. When given, a winch is only "
            "assessed if they include its files ([winch.data] files). Use the "
            "--changed-files={newFiles} form, so an empty list isn't dropped"
        ),
    )
    hook.add_argument(
        "--min-interval",
        metavar="DURATION",
        type=_duration,
        help=(
            "skip a winch whose wire log is newer than this, e.g. 6h (s, m, h or d), so a long "
            "cruise is reassessed at most this often; leave it out of a preFinalizeCurrentCruise "
            "hook, which writes the final wire logs"
        ),
    )
    hook.add_argument(
        "--theme",
        metavar="THEME_TOML",
        type=Path,
        help=(
            "theme file for the PDF, figures and notebook; the site config's [theme] applies on "
            "top"
        ),
    )
    hook.add_argument(
        "--execute-notebook",
        action="store_true",
        help="run each winch's notebook and save its outputs (needs nbclient and ipykernel)",
    )
    hook.add_argument(
        "--wire-record",
        metavar="RECORDS_DIR",
        type=Path,
        help=(
            "merge each winch's deployments into its tension member's wire record, "
            "RECORDS_DIR/<NSF-ID>/, replacing the cruise's earlier ones (outside the cruise, "
            "so not in its MD5 summary)"
        ),
    )
    # OpenVDM shows the hook's failure message, so every error is one line (cli.main)
    hook.set_defaults(handler=_openvdm, every_error_one_line=True)

    from_openvdm = commands.add_parser(
        "config-from-openvdm",
        help="make each winch's config TOML from OpenVDM's current cruise, without assessing",
        description=(
            "Merges OpenVDM's current cruise with the site config's winches, as the openvdm "
            "hook does, and writes a config TOML for each for review and `run -c`. Reports "
            "what's missing or assumed."
        ),
    )
    _add_openvdm_arguments(from_openvdm)
    from_openvdm.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_DIR",
        type=Path,
        default=Path("configs"),
        help=(
            "directory for a <CRUISE>_<WINCH>.toml for each winch, or - for stdout with one "
            "winch (default %(default)s)"
        ),
    )
    from_openvdm.add_argument(
        "--force", action="store_true", help="overwrite an existing config TOML"
    )
    from_openvdm.set_defaults(handler=_config_from_openvdm)

    site = commands.add_parser(
        "site-config",
        help="make the per-ship site TOML for the openvdm hook, by prompting for each field",
        description=(
            "Prompts for the per-ship settings OpenVDM doesn't store: the vessel name and "
            "contact (unless openvdm.yaml has them, from OpenVDM 2.17), the outputs' extra "
            "directory, and each winch: its data format and files, its transfer, and its "
            "tension member. Sheaves, monitoring and break tests are edited in the file. "
            "Works offline. Over an existing file (with --force), its values are the defaults."
        ),
    )
    site.add_argument(
        "-o",
        "--output",
        metavar="SITE_TOML",
        type=Path,
        help=(
            f"site TOML to write, or - for stdout (default {DEFAULT_SITE_CONFIG}, the install's, "
            "where the openvdm hook reads it)"
        ),
    )
    site.add_argument(
        "--openvdm-config",
        metavar="OPENVDM_YAML",
        type=Path,
        help=(
            "openvdm.yaml whose vessel settings (OpenVDM 2.17) are the defaults where the site "
            f"TOML has none (default {DEFAULT_OPENVDM_CONFIG}, when it exists)"
        ),
    )
    site.add_argument("--force", action="store_true", help="overwrite an existing site TOML")
    site.set_defaults(handler=_site_config)

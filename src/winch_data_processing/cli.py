"""Command line interface: ``winch-data-processing <command>``.

Conventions, as in nav-qa-processing:
- ``-o/--output`` is a directory. Existing outputs aren't overwritten without ``--force``, and a
  command checks that before it does any work.
- Arguments that take a value have an uppercase metavar, and every default is in the help.
- ``-v/--verbose``, before or after the command, logs more: ``-v`` is INFO and ``-vv`` DEBUG
  (default WARNING). Expected errors (a config that can't be used, a missing directory) are one
  line on stderr and exit 1; ``-vv`` adds their traceback.
- Integrations (``integrations/``) add their own commands with ``add_commands`` and their
  expected errors with ``EXPECTED_ERRORS``; this is the one place core meets them. The OpenVDM
  commands (``openvdm``, ``config-from-openvdm``, ``site-config``) are named as in
  nav-qa-processing and sbe-qa-processing, so a ship's ``openvdm.yaml`` calls them alike. In
  ``openvdm`` mode every error, expected or not, is one line, because OpenVDM shows it.
"""

import argparse
import logging
import sys
import traceback
from pathlib import Path

from winch_data_processing import __version__, formats
from winch_data_processing.assess import WinchResult, assess
from winch_data_processing.checks import Status
from winch_data_processing.clitools import print_written, refuse_overwrite
from winch_data_processing.config import ConfigError, load_config
from winch_data_processing.integrations.openvdm import cli as openvdm_cli
from winch_data_processing.network import NetworkError
from winch_data_processing.rvss import StandardError
from winch_data_processing.theme import ThemeError
from winch_data_processing.units import UnitError
from winch_data_processing.wirelog import stem

logger = logging.getLogger(__name__)
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)
# The integrations whose commands this CLI adds (each module's add_commands), in help order
INTEGRATIONS = (openvdm_cli,)
EXPECTED_ERRORS = (
    ConfigError,
    StandardError,
    UnitError,
    NetworkError,
    FileNotFoundError,
    NotADirectoryError,
    FileExistsError,
    ThemeError,
    *(error for integration in INTEGRATIONS for error in integration.EXPECTED_ERRORS),
)
MARKS = {
    Status.PASS: "pass",
    Status.FAIL: "FAIL",
    Status.WARN: "warn",
    Status.INFO: "info",
    Status.NA: "n/a",
}


def _add_verbose(parser: argparse.ArgumentParser) -> None:
    """-v/--verbose on parser and, so it also works after the command, on every subcommand,
    the integrations' included. SUPPRESS, so a subcommand doesn't reset a -v given before it
    """
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        help="log more: -v INFO, -vv DEBUG (default WARNING)",
    )
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for command in action.choices.values():
                _add_verbose(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="winch-data-processing",
        description="Wire logs and RVSS Appendix A checks from oceanographic winch data",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    run = commands.add_parser(
        "run",
        help="read a cruise's winch data, find its deployments, "
        "write its wire log and check it against RVSS Appendix A",
    )
    run.add_argument("data_dir", metavar="DATA_DIR", type=Path, help="the winch's raw files")
    run.add_argument(
        "-c",
        "--config",
        metavar="CONFIG",
        type=Path,
        required=True,
        help="the config TOML (configs/config.example.toml)",
    )
    run.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT",
        type=Path,
        default=Path("output"),
        help="directory for the outputs (default: output)",
    )
    run.add_argument("--force", action="store_true", help="overwrite existing outputs")
    run.add_argument(
        "--theme",
        metavar="THEME_TOML",
        type=Path,
        help="theme file for the figures; the config's [theme] applies on top",
    )
    run.set_defaults(handler=_run)

    listing = commands.add_parser("formats", help="list the data formats")
    listing.set_defaults(handler=_formats)

    # The integrations' commands: this is the one place core's CLI meets them
    for integration in INTEGRATIONS:
        integration.add_commands(commands)
    _add_verbose(parser)
    return parser


def summary(result: WinchResult) -> list[str]:
    """The run's summary: its records and deployments, then each check"""
    lines = [
        f"{stem(result.config)}: {result.data.records} records, {len(result.casts)} deployments"
    ]
    lines += [
        f"  {MARKS[check.status]:4}  {check.name} ({check.section}): {check.detail}"
        for check in result.checks
    ]
    return lines


def _run(args) -> int:
    config = load_config(args.config)
    if not args.data_dir.is_dir():
        raise NotADirectoryError(f"{args.data_dir} is not a directory")
    from winch_data_processing.reports import report_paths, write_reports  # matplotlib
    from winch_data_processing.theme import resolve_theme

    refuse_overwrite(report_paths(config, args.output).values(), args.force)
    theme = resolve_theme(args.theme, config)  # before the work: a bad theme fails first
    result = assess(args.data_dir, config)
    for error in result.data.errors:
        print(f"unread: {error}", file=sys.stderr)
    print_written(write_reports(result, args.output, theme))
    print("\n".join(summary(result)))
    return 1 if result.failed else 0


def _formats(args) -> int:
    for name, reader in formats.READERS.items():
        print(f"{name:18} {reader.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    level = LOG_LEVELS[min(getattr(args, "verbose", 0), len(LOG_LEVELS) - 1)]
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("winch_data_processing").setLevel(level)
    try:
        return args.handler(args)
    except Exception as error:
        # A command that sets every_error_one_line (the OpenVDM hook, whose failure message
        # OpenVDM shows) reports even an unexpected error in one line
        hook = getattr(args, "every_error_one_line", False)
        if not hook and not isinstance(error, EXPECTED_ERRORS):
            raise
        if level <= logging.DEBUG:
            traceback.print_exc()
        message = f"{type(error).__name__}: {error}" if hook else str(error)
        print(f"winch-data-processing: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

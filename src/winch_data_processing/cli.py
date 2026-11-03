"""Command line interface: ``winch-data-processing <command>``.

Conventions, as in nav-qa-processing:
- ``-o/--output`` is a directory. Existing outputs aren't overwritten without ``--force``, and a
  command checks that before it does any work.
- Arguments that take a value have an uppercase metavar, and every default is in the help.
- ``-v/--verbose``, before or after the command, logs more: ``-v`` is INFO and ``-vv`` DEBUG
  (default WARNING). Expected errors (a config that can't be used, a missing directory) are one
  line on stderr and exit 1; ``-vv`` adds their traceback.
"""

import argparse
import logging
import sys
import traceback
from pathlib import Path

from winch_data_processing import __version__, formats
from winch_data_processing.assess import assess
from winch_data_processing.checks import Status
from winch_data_processing.config import ConfigError, load_config
from winch_data_processing.rvss import StandardError
from winch_data_processing.units import UnitError
from winch_data_processing.wirelog import output_paths, stem, write_outputs

logger = logging.getLogger(__name__)
LOG_LEVELS = (logging.WARNING, logging.INFO, logging.DEBUG)
EXPECTED_ERRORS = (ConfigError, StandardError, UnitError, FileNotFoundError, NotADirectoryError)
MARKS = {
    Status.PASS: "pass",
    Status.FAIL: "FAIL",
    Status.WARN: "warn",
    Status.INFO: "info",
    Status.NA: "n/a",
}


def build_parser() -> argparse.ArgumentParser:
    verbose = argparse.ArgumentParser(add_help=False)
    verbose.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        help="log more: -v INFO, -vv DEBUG (default WARNING)",
    )
    parser = argparse.ArgumentParser(
        prog="winch-data-processing",
        description="Wire logs and RVSS Appendix A checks from oceanographic winch data",
        parents=[verbose],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    run = commands.add_parser(
        "run",
        parents=[verbose],
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
    run.set_defaults(handler=_run)

    listing = commands.add_parser("formats", parents=[verbose], help="list the data formats")
    listing.set_defaults(handler=_formats)
    return parser


def _run(args) -> int:
    config = load_config(args.config)
    if not args.data_dir.is_dir():
        raise NotADirectoryError(f"{args.data_dir} is not a directory")
    paths = output_paths(config, args.output).values()
    existing = [] if args.force else [p for p in paths if p.exists()]
    if existing:
        names = ", ".join(str(p) for p in existing)
        raise FileExistsError(f"{names} exists; --force to overwrite")
    result = assess(args.data_dir, config)
    for error in result.data.errors:
        print(f"unread: {error}", file=sys.stderr)
    for path in write_outputs(result, args.output):
        print(f"wrote {path}")
    print(f"{stem(config)}: {len(result.data)} records, {len(result.casts)} deployments")
    for check in result.checks:
        print(f"  {MARKS[check.status]:4}  {check.name} ({check.section}): {check.detail}")
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
    except (*EXPECTED_ERRORS, FileExistsError) as error:
        if level <= logging.DEBUG:
            traceback.print_exc()
        print(f"winch-data-processing: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

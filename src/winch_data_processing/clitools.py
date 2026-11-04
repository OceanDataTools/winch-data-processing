"""What the commands share, core's and the integrations': refusing to overwrite, writing a config
TOML, listing what was written. The conventions are in cli.py.
"""

import argparse
import sys
from pathlib import Path

from winch_data_processing.wirelog import safe_name

STDOUT = "-"


def refuse_overwrite(paths, force: bool) -> None:
    """FileExistsError (one line, exit 1) when any of paths exists, unless force"""
    existing = [] if force else [p for p in paths if str(p) != STDOUT and Path(p).exists()]
    if existing:
        names = ", ".join(str(p) for p in existing)
        raise FileExistsError(f"{names} exists; --force to overwrite")


def config_toml_path(output: Path, name: str) -> Path:
    """<output>/<name>.toml, a valid file name whatever the name"""
    return Path(output) / f"{safe_name(name)}.toml"


def write_config_toml(text: str, name: str, args: argparse.Namespace) -> None:
    """text as <output>/<name>.toml, or to stdout for -o -"""
    if str(args.output) == STDOUT:
        print(text, end="")
        return
    destination = config_toml_path(args.output, name)
    refuse_overwrite([destination], args.force)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)
    print(f"wrote {destination}", file=sys.stderr)


def print_written(written: list[Path]) -> None:
    for path in written:
        print(f"wrote {path}")


def plural(n: int, thing: str) -> str:
    """ "1 deployment", "2 deployments", with thousands separated"""
    return f"{n:,} {thing}" if n == 1 else f"{n:,} {thing}s"

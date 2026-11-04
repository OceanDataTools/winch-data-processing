"""The one entry point: `assess(directory, config)` -> `WinchResult`, which every writer takes"""

from dataclasses import dataclass
from pathlib import Path

from winch_data_processing.casts import Cast, find_casts
from winch_data_processing.checks import Check, Status, run_checks
from winch_data_processing.config import Config
from winch_data_processing.rawdata import WinchData, read_fileset


@dataclass
class WinchResult:
    config: Config
    data: WinchData
    casts: list[Cast]
    checks: list[Check]
    directory: Path | None = None  # the data directory read

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.FAIL]


def assess(directory: Path | str, config: Config) -> WinchResult:
    data = read_fileset(directory, config)
    casts = find_casts(data, config.thresholds)
    return WinchResult(config, data, casts, run_checks(config, data, casts), Path(directory))

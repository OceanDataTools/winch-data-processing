"""RVSS Appendix A, "UNOLS Rope and Cable Safe Working Standards", as data.

From the RVSS 11th edition (November 2021, Appendix A rewritten June 2023, last updated
25 June 2026). Section numbers are the appendix's. Only what a winch's data or its config can
show is here; the rest (deck safety, danger zones, operator certification, termination plans)
is the operator's paperwork, and `docs/rvss-appendix-a.md` says which is which.

The factor of safety (FS) is the Assigned Breaking Load over the tension, ABL / T (A.2.10,
A.2.19). The ABL is the lower of the Nominal and the latest Tested Breaking Load (A.2.1, A.5.1.1).
The operator selects an FS for each tension member (A.5.1.1); the table whose range holds it
(A.8.1 to A.8.5) sets the monitoring, alarms, sheaves and testing it requires, and the
deployment must be halted when the selected FS is reached.
"""

from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    """What the tension member is (A.2.3, A.2.18)"""

    CABLE = "cable"  # steel armored, with conductors or fiber
    WIRE_ROPE = "wire_rope"  # steel, no conductors
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class Table:
    """One of Tables A.8.1 to A.8.5: the requirements for an FS from `min_fs` up to (not
    including) `below_fs`, or any FS from `min_fs` when `below_fs` is None
    """

    name: str
    kinds: frozenset[Kind]
    min_fs: float  # the deployment must be halted here
    below_fs: float | None
    display_hz: float | None = None  # tension display refresh rate at the operator's station
    logging_hz: float | None = None  # minimum tension logging rate
    trend_graph: bool = False  # "tension trending" graph at the operator's station
    calibration_months: int | None = None  # tension measuring system calibration interval
    accuracy_percent: float | None = None  # of the applied load
    alarm_fs: float | None = None  # audible and visual alarms before this FS
    alarms_logged: bool = False  # alarm conditions included in the logged data
    min_d_ratio: float | None = None  # D/d, sheaves and load-carrying rollers
    min_d1_ratio: float | None = None  # D/d1
    max_groove_ratio: float | None = None  # groove w/d
    break_test_months: int | None = None  # sample break tests, every…
    break_test_months_after_decrease: int | None = None  # …or this after a 10 % ABL decrease

    def holds(self, fs: float) -> bool:
        return fs >= self.min_fs and (self.below_fs is None or fs < self.below_fs)


STEEL = frozenset({Kind.CABLE, Kind.WIRE_ROPE})

A_8_1 = Table(
    "Table A.8.1",
    STEEL,
    min_fs=5.0,
    below_fs=None,
    break_test_months=24,
    break_test_months_after_decrease=12,
)
A_8_2 = Table(
    "Table A.8.2",
    STEEL,
    min_fs=2.5,
    below_fs=5.0,
    display_hz=3.0,
    logging_hz=3.0,
    calibration_months=6,
    accuracy_percent=4.0,
    alarm_fs=2.8,
    alarms_logged=True,
    min_d_ratio=40.0,
    min_d1_ratio=400.0,
    max_groove_ratio=1.5,
    break_test_months=24,
    break_test_months_after_decrease=12,
)
_HIGH_RESOLUTION = {
    "display_hz": 10.0,
    "logging_hz": 20.0,
    "trend_graph": True,
    "calibration_months": 6,
    "accuracy_percent": 3.0,
    "alarms_logged": True,
    "min_d_ratio": 40.0,
    "min_d1_ratio": 400.0,
    "break_test_months": 12,
    "break_test_months_after_decrease": 6,
}
# Grooving "per Ref A.1.1, Chapter 1, and Section 11.0" from here: no ratio to check
A_8_3 = Table("Table A.8.3", STEEL, min_fs=2.0, below_fs=2.5, alarm_fs=2.2, **_HIGH_RESOLUTION)
A_8_4 = Table(
    "Table A.8.4", frozenset({Kind.WIRE_ROPE}), 1.5, 2.0, alarm_fs=1.7, **_HIGH_RESOLUTION
)
# Synthetics: FS 5.0 or greater unless the Wire Pool evaluates and approves the operation
# (A.8.3.1); alarms at 12 % above the minimum FS (A.8.3.3); tested before and after a planned
# use, which has no interval to check here
A_8_5 = Table(
    "Table A.8.5",
    frozenset({Kind.SYNTHETIC}),
    min_fs=5.0,
    below_fs=None,
    display_hz=10.0,
    logging_hz=20.0,
    trend_graph=True,
    calibration_months=6,
    accuracy_percent=3.0,
    alarm_fs=5.6,
    alarms_logged=True,
    min_d_ratio=40.0,
)
TABLES = (A_8_1, A_8_2, A_8_3, A_8_4, A_8_5)

# The lowest FS each kind may be operated to (A.8.3: cables halt at 2.0; A.8.4: wire rope at
# 1.5; A.8.3.1: synthetics at 5.0 without the Wire Pool's approval)
MIN_FS = {Kind.CABLE: 2.0, Kind.WIRE_ROPE: 1.5, Kind.SYNTHETIC: 5.0}
# Peak tension over sheaves beyond the elastic limit retires the length (A.5.6, A.6.2)
ELASTIC_LIMIT_FS = {Kind.CABLE: 1.8, Kind.WIRE_ROPE: 1.33}
RETIRE_ABL_FRACTION = 0.5  # an ABL below half the NBL retires the length (A.5.6)
ABL_DECREASE = 0.10  # a 10 % decrease in ABL shortens the break test interval


class StandardError(ValueError):
    """A config the standard doesn't allow, e.g. a cable at FS 1.8"""


def table_for(kind: Kind, fs: float) -> Table:
    """The table whose requirements apply to a tension member of `kind` at the selected `fs`"""
    if fs < MIN_FS[kind]:
        raise StandardError(
            f"a {kind.replace('_', ' ')} may not be operated below FS {MIN_FS[kind]:g} "
            f"(RVSS Appendix A); the selected FS is {fs:g}"
        )
    for table in TABLES:
        if kind in table.kinds and table.holds(fs):
            return table
    raise StandardError(f"no table of RVSS Appendix A holds a {kind} at FS {fs:g}")

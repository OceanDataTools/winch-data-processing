"""The project-wide quantity table.

Every value this project writes (the wire log, the checks, and later the PDF, figures and
notebook) is defined here once: name, description, datatype, unit and format. Writers format
values and label units only through this table; they never contain their own format strings or
unit names. The rules, as in nav-qa-processing:
- Units are CF/UDUNITS strings. `1` is the unit of every dimensionless value.
- Floats use fixed decimals and are never trimmed; integers are written without decimals.
- Datetimes are UTC, `YYYY-MM-DDThh:mm:ss.fffZ`.
- Missing values are written `NAN`.
- Descriptions contain no commas, so a list of them can be one CSV field.

Tension is in pound-force throughout: the unit of the NSF Wire Pool's breaking loads and of most
fleet tensiometers. Readers and configs in other units are converted on the way in (`units.py`).
"""

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

MISSING = "NAN"


class Datatype(StrEnum):
    DATETIME = "datetime"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"


@dataclass(frozen=True)
class Quantity:
    """One quantity: what it is, its unit and how its values are written. `decimals` is a
    float's fixed precision or a datetime's fractional-second digits
    """

    name: str
    description: str
    datatype: Datatype
    unit: str | None = None
    decimals: int = 0

    @property
    def label(self) -> str:
        """Description and unit for table headers and axes, e.g. "Tension [lbf]"; a
        dimensionless quantity shows no unit
        """
        if self.unit in (None, "1"):
            return self.description
        return f"{self.description} [{self.unit}]"

    def format(self, value) -> str:
        """`value` as text in this quantity's format, or MISSING"""
        if value is None:
            return MISSING
        if self.datatype is Datatype.DATETIME:
            if np.isnat(np.datetime64(value, "ns")):
                return MISSING
            return format_time(np.datetime64(value, "ns"), self.decimals)
        if self.datatype is Datatype.STRING:
            return str(value)
        if isinstance(value, float | np.floating) and not math.isfinite(value):
            return MISSING
        if self.datatype is Datatype.INTEGER:
            return str(int(value))
        return f"{float(value):.{self.decimals}f}"


def format_time(value: np.datetime64, decimals: int = 3) -> str:
    """`value` as `YYYY-MM-DDThh:mm:ss[.f…]Z`, truncated to `decimals` fractional digits"""
    text = str(np.datetime64(value, "ns"))  # YYYY-MM-DDThh:mm:ss.fffffffff
    whole, _, fraction = text.partition(".")
    return f"{whole}.{fraction[:decimals]}Z" if decimals else f"{whole}Z"


TIME = Quantity("time", "Time", Datatype.DATETIME, decimals=3)
TENSION = Quantity("tension", "Tension", Datatype.FLOAT, "lbf", 0)
PAYOUT = Quantity("payout", "Payout", Datatype.FLOAT, "m", 1)
SPEED = Quantity("speed", "Wire speed", Datatype.FLOAT, "m min-1", 1)
FACTOR_OF_SAFETY = Quantity("factor_of_safety", "Factor of safety", Datatype.FLOAT, "1", 2)
DURATION = Quantity("duration", "Duration", Datatype.FLOAT, "s", 1)
LOGGING_RATE = Quantity("logging_rate", "Logging rate", Datatype.FLOAT, "Hz", 1)
COUNT = Quantity("count", "Count", Datatype.INTEGER, "1")
DIAMETER = Quantity("diameter", "Diameter", Datatype.FLOAT, "in", 3)
RATIO = Quantity("ratio", "Ratio", Datatype.FLOAT, "1", 1)

# The wire log's per-deployment columns (A.5.2.1, A.5.3.1): name, then the quantity that
# formats it, with its own description
CAST = Quantity("cast", "Deployment number", Datatype.INTEGER, "1")
START = Quantity("start", "Deployment start", Datatype.DATETIME, decimals=3)
END = Quantity("end", "Deployment end", Datatype.DATETIME, decimals=3)
MAX_PAYOUT = Quantity("max_payout", "Maximum payout", Datatype.FLOAT, "m", 1)
MAX_TENSION = Quantity("max_tension", "Maximum tension", Datatype.FLOAT, "lbf", 0)
MAX_TENSION_TIME = Quantity(
    "max_tension_time", "Time of maximum tension", Datatype.DATETIME, decimals=3
)
PAYOUT_AT_MAX_TENSION = Quantity(
    "payout_at_max_tension", "Line out at maximum tension", Datatype.FLOAT, "m", 1
)
MIN_FACTOR_OF_SAFETY = Quantity(
    "min_factor_of_safety", "Minimum factor of safety", Datatype.FLOAT, "1", 2
)
SAMPLES = Quantity("samples", "Tension samples", Datatype.INTEGER, "1")
LONGEST_GAP = Quantity("longest_gap", "Longest logging gap", Datatype.FLOAT, "s", 2)
ALARM_SAMPLES = Quantity(
    "alarm_samples", "Samples beyond the alarm tension", Datatype.INTEGER, "1"
)
SWT_SAMPLES = Quantity(
    "swt_samples", "Samples beyond the safe working tension", Datatype.INTEGER, "1"
)

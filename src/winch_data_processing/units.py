"""The units data and configs may use, and their factors to the project's (`quantities.py`):
tension in lbf, payout in m, speed in m min-1, diameters in inches. Units are CF/UDUNITS
spellings; a config naming another is an error listing these
"""

TENSION = {"lbf": 1.0, "N": 0.224808943, "kN": 224.808943, "kgf": 2.20462262}
LENGTH = {"m": 1.0, "ft": 0.3048}
SPEED = {"m min-1": 1.0, "m s-1": 60.0, "ft min-1": 0.3048}
DIAMETER = {"in": 1.0, "mm": 1 / 25.4}


class UnitError(ValueError):
    pass


def factor(unit: str, table: dict[str, float], what: str) -> float:
    """The factor taking `unit` to the project's unit for `what`"""
    try:
        return table[unit]
    except KeyError:
        known = ", ".join(f'"{u}"' for u in table)
        raise UnitError(f'{what} unit "{unit}" is not one of {known}') from None


def convert(values, unit: str, table: dict[str, float], what: str):
    """`values` (a number or an array) in the project's unit for `what`"""
    scale = factor(unit, table, what)
    return values * scale if scale != 1.0 else values

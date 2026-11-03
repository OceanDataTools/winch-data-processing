from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "configs" / "config.example.toml"

CONFIG = """
[cruise]
id = "TEST01"

[winch]
name = "CTD"

[tension_member]
id = "NSF-TEST"
kind = "cable"
nominal_breaking_load = 10000
factor_of_safety = 2.5
diameter = 0.322
largest_wire_diameter = 0.0375

[[tension_member.break_test]]
date = 2026-01-10
load = 10000

[[sheave]]
name = "Block"
tread_diameter = 16.0
groove_width = 0.40

[monitoring]
display_rate = 10
last_calibration = 2026-05-01
accuracy = 2.0
alarm_tension = 3500

[data]
format = "openrvdas_lci90i"
files = "*.txt"
"""


def cast(
    start: datetime,
    rate: float = 20.0,
    max_payout: float = 1000.0,
    speed: float = 60.0,  # [m min-1]
    base: float = 800.0,  # [lbf] the package
    per_metre: float = 0.3,  # [lbf m-1] the cable in water
    swell: float = 100.0,  # [lbf] amplitude, 8 s period
    soak: float = 120.0,  # [s] on deck before and after
) -> list[tuple[datetime, float, float, float]]:
    """A CTD cast's (time, tension, payout, speed) at `rate`: on deck, down, up, on deck"""
    down = max_payout / (speed / 60)
    total = 2 * soak + 2 * down
    t = np.arange(0, total, 1 / rate)
    payout = np.clip(np.minimum(t - soak, total - soak - t) * speed / 60, 0, max_payout)
    velocity = np.where(t < soak + down, speed, -speed) * (payout > 0)
    tension = base + per_metre * payout + swell * np.sin(2 * np.pi * t / 8) * (payout > 0)
    return [
        (start + timedelta(seconds=float(s)), float(T), float(p), float(v))
        for s, T, p, v in zip(t, tension, payout, velocity, strict=True)
    ]


def rd(time: str, tension: float, speed: float, payout: float, lan: str = "02") -> str:
    """An LCI-90i RD record with its checksum, as RR2403's displays wrote them"""
    body = f"{lan}RD,{time},{tension:08.0f},{speed:08.0f},{payout:08.1f},"
    return f"{body}{sum(body.encode())}"


def lci_lines(rows, lan: str = "02", logged: bool = True) -> list[str]:
    """LCI-90i records, led by OpenRVDAS's timestamp when `logged`"""
    lines = []
    for time, tension, payout, speed in rows:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.%f")
        record = rd(stamp[:-3], tension, speed, payout, lan)
        lines.append(f"{stamp}Z {record}" if logged else record)
    return lines


def write_cruise(directory: Path, *casts_rows, lan: str = "02") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    rows = [row for rows in casts_rows for row in rows]
    (directory / "winch.txt").write_text("\n".join(lci_lines(rows, lan)) + "\n")
    return directory


START = datetime(2026, 6, 19, 12)

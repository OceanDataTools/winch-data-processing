"""RR2403's one hydro cast (R2R fileset 160178, Revelle's Markey DESH-5 and its LCI-90i), 21:50
to 22:40 UTC on 2024-04-06: the counter at a stale 664.5 m until zeroed at 22:01, a cast to
500 m, and the recovery. The tension member's values are assumed (R2R doesn't record them)
"""

import lzma

import numpy as np
import pytest
from conftest import CONFIG, ROOT

from winch_data_processing.assess import assess
from winch_data_processing.checks import Status
from winch_data_processing.config import loads_config

FIXTURE = ROOT / "tests" / "data" / "RR2403_winch_lci90i_hydro-2024-04-06_cast.txt.xz"


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    directory = tmp_path_factory.mktemp("RR2403")
    (directory / FIXTURE.stem).write_bytes(lzma.decompress(FIXTURE.read_bytes()))
    config = CONFIG.replace('id = "TEST01"', 'id = "RR2403"').replace(
        'files = "*.txt"', 'files = "*hydro*.txt"'
    )
    return assess(directory, loads_config(config))


def test_its_records(result):
    data = result.data
    assert data.records == 59_681
    assert len(data) == 36_857  # the cast's 36,814 and the idle payout's changes
    assert len(data.envelope) == 50 and data.envelope.tension_max.max() == 3593  # 21:50 to 22:40
    assert data.skipped == {FIXTURE.stem: {"bad checksum": 23}}
    assert data.names == {"02": 59_681}
    assert data.backwards == 0
    assert np.nanmax(data.tension) == 3593


def test_its_one_cast(result):
    (cast,) = result.casts
    assert str(cast.start_time) == "2024-04-06T21:58:25.013921000"  # the lift, before the reset
    assert str(cast.end_time) == "2024-04-06T22:29:15.473814000"
    assert cast.max_payout == 499.8  # the deployed stretch's, not the stale 664.5
    assert cast.max_tension == 3593
    assert str(cast.max_tension_time) == "2024-04-06T22:25:25.794032000"
    assert cast.payout_at_max_tension == -6.4  # at the surface, on recovery
    assert cast.logging_rate == pytest.approx(19.9, abs=0.05)  # the LCI-90i's 20 Hz, a little slow
    assert cast.longest_gap == pytest.approx(0.1, abs=0.01)


def test_its_checks(result):
    checks = {c.name: c for c in result.checks}
    assert checks["Tension within the safe working tension"].status is Status.PASS
    assert checks["Tension logging rate"].status is Status.PASS
    # 3,593 lbf on a 10,000 lbf ABL is FS 2.78: past the A.8.2 alarm point, FS 2.8
    alarm = 10_000 / 2.8
    assert result.casts[0].max_tension > alarm

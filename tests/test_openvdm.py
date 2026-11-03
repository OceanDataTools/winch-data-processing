"""The OpenVDM hook, against a stand-in OpenVDM API and an OpenVDM-style cruise directory with an
OpenRVDAS collection system logging two winches' LCI-90i displays (as nav-qa-processing's
tests/test_openvdm.py).
"""

import csv
import json
import subprocess
import sys
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, START, cast, lci_lines

from winch_data_processing.cli import main
from winch_data_processing.config import ConfigError, Thresholds, load_config, loads_config
from winch_data_processing.integrations.openvdm import hook as openvdm

WINCH = """
[[winch]]
name = "{name}"
transfer = "{transfer}"
manufacturer = "Markey"
model = "DESH-5"

[winch.tension_member]
id = "NSF-{name}"
kind = "cable"
nominal_breaking_load = 10000
factor_of_safety = 2.5
diameter = 0.322
largest_wire_diameter = 0.0375

[[winch.sheave]]
name = "Block"
tread_diameter = 16.0
groove_width = 0.40

[winch.monitoring]
display_rate = 10
last_calibration = 2026-05-01
accuracy = 2.0
alarm_tension = 3500

[winch.data]
format = "openrvdas_lci90i"
files = "raw/{name}-*.txt"
"""
SITE = '[vessel]\nname = "Sally Ride"\n' + WINCH.format(name="CTD", transfer="OpenRVDAS")
TWO_WINCHES = SITE + WINCH.format(name="trawl", transfer="OpenRVDAS")


def cruise_config(base_dir: Path, cruise_id="SR2612") -> dict:
    """A getCruiseConfig response, trimmed to what the hook reads"""
    return {
        "cruiseID": cruise_id,
        "cruiseName": "Sally Ride engineering cruise",
        "cruisePI": "A. Scientist",
        "cruiseLocation": "San Diego Trough",
        "cruiseStartDate": "2026/06/19 00:00",
        "cruiseEndDate": "2026/06/21 00:00",
        "warehouseConfig": {
            "shipboardDataWarehouseBaseDir": str(base_dir),
            "shipboardDataWarehouseUsername": "survey",
            "md5SummaryFn": "MD5_Summary.txt",
            "cruiseConfigFn": "cruise_config.json",
        },
        "collectionSystemTransfersConfig": [
            {"collectionSystemTransferID": "4", "name": "CTD", "destDir": "CTD"},
            {"collectionSystemTransferID": "5", "name": "OpenRVDAS", "destDir": "OpenRVDAS"},
        ],
        "extraDirectoriesConfig": [
            {"name": "Dashboard_Data", "destDir": "OpenVDM/DashboardData", "enable": "1"},
            {"name": "WINCH_QA", "destDir": "Products/WINCH_QA", "enable": "1"},
        ],
    }


@pytest.fixture(autouse=True)
def no_installed_site_toml(monkeypatch, tmp_path):
    """The hook reads the checkout's configs/site.toml without --site-config; not in these tests"""
    monkeypatch.setattr(openvdm, "DEFAULT_SITE_CONFIG", tmp_path / "installed" / "site.toml")


def site_config(tmp_path, text=SITE) -> Path:
    path = tmp_path / "site.toml"
    path.write_text(text)
    return path


def site(text: str, tmp_path: Path) -> openvdm.SiteConfig:
    return openvdm.load_site_config(site_config(tmp_path, text))


def openrvdas(directory: Path, name="CTD", base: float = 800.0) -> Path:
    """OpenRVDAS's log of a winch's LCI-90i: two casts on 2026-06-19 at 20 Hz, and a met
    station's log beside it. A base above 4000 lbf, the SWT, fails "within the SWT"
    """
    raw = directory / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    first = cast(START, max_payout=500, base=base)
    second = cast(first[-1][0] + timedelta(minutes=30), max_payout=800, base=base)
    log = raw / f"{name}-2026-06-19.txt"
    log.write_text("\n".join(lci_lines(first + second)) + "\n")
    (raw / "MET-2026-06-19.txt").write_text("2026-06-19T12:00:00.0Z,WIR,1,2,3\n")
    return log


# The site TOML ---------------------------------------------------------------------------------


def test_the_example_site_config_loads():
    example = openvdm.load_site_config(ROOT / "configs/site.example.toml")
    assert [(w.name, w.transfer, w.files) for w in example.winches] == [
        ("hydro", "OpenRVDAS", "*hydro*.txt"),
        ("trawl", "OpenRVDAS", "*trawl*.txt"),
    ]
    assert example.output_extra_directory == "WINCH_QA"


def test_site_config_round_trips(tmp_path):
    example = openvdm.load_site_config(ROOT / "configs/site.example.toml")
    path = site_config(tmp_path, openvdm.site_config_to_toml(example))
    assert openvdm.load_site_config(path) == example


@pytest.mark.parametrize(
    "text, message",
    [
        ('[cruise]\nid = "X"\n', "a site TOML has no [cruise]"),
        ('[winch]\nname = "CTD"\n', "winch is an array of tables"),
        ("[openvdm]\noutput_dir = 1\n", "unknown key 'openvdm.output_dir'"),
        (SITE + "[winch.data.extra]\n", "unknown key 'winch.data.extra'"),
        (SITE.replace('name = "CTD"', 'name = ""'), "[[winch]] 1 needs name"),
        (SITE + WINCH.format(name="CTD", transfer=""), "two [[winch]] tables are named 'CTD'"),
        # A winch that can't make a config is an error as the site TOML is read, naming it
        (SITE.replace("factor_of_safety = 2.5", "factor_of_safety = 1.8"), "[[winch]] CTD: a"),
        (SITE.replace('format = "openrvdas_lci90i"', 'format = "x"'), '"x" is not one of'),
    ],
)
def test_a_site_config_that_cant_be_used(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        openvdm.load_site_config(site_config(tmp_path, text))


def test_winches_for_a_transfer(tmp_path):
    text = SITE + WINCH.format(name="trawl", transfer="Winches")
    text += WINCH.format(name="any", transfer="").replace('transfer = ""\n', "")
    loaded = openvdm.load_site_config(site_config(tmp_path, text))
    assert [w.name for w in loaded.winches_for("OpenRVDAS")] == ["CTD", "any"]
    assert [w.name for w in loaded.winches_for("Winches")] == ["trawl", "any"]


# Unit behavior ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("2026/07/09 14:30", date(2026, 7, 9)), ("2026/07/09", date(2026, 7, 9)), ("", None)],
)
def test_parse_openvdm_date(text, expected):
    assert openvdm.parse_openvdm_date(text) == expected


@pytest.mark.parametrize(
    "changed, files, expected",
    [
        ([""], "raw/CTD-*.txt", False),
        (["raw/MET-2026-06-19.txt"], "raw/CTD-*.txt", False),
        (["raw/MET-2026-06-19.txt raw/CTD-2026-06-19.txt"], "raw/CTD-*.txt", True),
        (["OpenRVDAS/raw/CTD-2026-06-19.txt"], "raw/CTD-*.txt", True),  # a prefix
        (["raw/sub dir/CTD-x.txt"], "raw/CTD-*.txt", False),  # * stops at /
        (["raw/a/b/hydro_1.txt"], "**/hydro_?.txt", True),
        (["anything.txt"], "*", True),  # every file
        ([""], "*", False),
    ],
)
def test_has_winch_changes(changed, files, expected):
    assert openvdm.has_winch_changes(changed, files) is expected


@pytest.mark.parametrize(
    "block, expected",
    [
        (None, {}),
        ({"name": " Sally Ride ", "contact": {"email": ""}}, {"vessel_name": "Sally Ride"}),
        (
            {"name": "", "contact": {"institution": "SIO", "email": "a@b.org"}},
            {"contact_institution": "SIO", "contact_email": "a@b.org"},
        ),
    ],
)
def test_vessel_settings(block, expected):
    assert openvdm.vessel_settings(block, "openvdm.yaml").settings == expected


def test_resolve_paths_and_metadata(tmp_path):
    loaded = site(SITE, tmp_path)
    run = openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0])
    assert run.cruise_dir == tmp_path / "SR2612"
    assert run.fileset_dir == tmp_path / "SR2612/OpenRVDAS"
    assert run.output_dir == tmp_path / "SR2612/Products/WINCH_QA"
    assert run.owner == "survey" and run.notes == []
    config = run.config
    assert (config.cruise_id, config.winch_name, config.tension_member.id) == (
        "SR2612",
        "CTD",
        "NSF-CTD",
    )
    assert (config.cruise_name, config.cruise_pi, config.cruise_location) == (
        "Sally Ride engineering cruise",
        "A. Scientist",
        "San Diego Trough",
    )
    assert (config.cruise_start, config.cruise_end) == (date(2026, 6, 19), date(2026, 6, 21))
    assert config.vessel_name == "Sally Ride" and config.data.files == "raw/CTD-*.txt"


def test_the_sites_thresholds_under_a_winchs_own(tmp_path):
    text = SITE.replace(
        "[[winch]]", "[thresholds]\nmerge_gap = 60.0\ndeploy_payout = 5.0\n\n[[winch]]"
    )
    text += "\n[winch.thresholds]\ndeploy_payout = 20.0\n"
    loaded = site(text, tmp_path)
    run = openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0])
    assert run.config.thresholds == Thresholds(deploy_payout=20.0, merge_gap=60.0)


def test_resolve_explains_missing_configuration(tmp_path):
    loaded = site(SITE, tmp_path)
    with pytest.raises(openvdm.OpenVDMError, match="no collection system transfer named 'XBT'"):
        openvdm.resolve(cruise_config(tmp_path), loaded, "XBT", loaded.winches[0])
    loaded.output_extra_directory = "Winch_Logs"
    with pytest.raises(openvdm.OpenVDMError, match="no extra directory named 'Winch_Logs'"):
        openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0])
    config = cruise_config(tmp_path, cruise_id="")
    with pytest.raises(openvdm.OpenVDMError, match="no current cruise"):
        openvdm.resolve(config, loaded, "OpenRVDAS", loaded.winches[0])


def test_resolve_without_a_start_date(tmp_path):
    loaded = site(SITE, tmp_path)
    config = {**cruise_config(tmp_path), "cruiseStartDate": ""}
    run = openvdm.resolve(config, loaded, "OpenRVDAS", loaded.winches[0])
    assert run.config.cruise_start is None and run.config.cruise_end is None
    assert "no start date, so every record is read" in run.notes[0]


def test_open_ended_cruise_ends_today_in_utc(tmp_path, monkeypatch):
    monkeypatch.setattr(openvdm, "utc_today", lambda: date(2026, 6, 20))
    loaded = site(SITE, tmp_path)
    config = {**cruise_config(tmp_path), "cruiseEndDate": ""}
    run = openvdm.resolve(config, loaded, "OpenRVDAS", loaded.winches[0])
    assert run.config.cruise_end == date(2026, 6, 20)
    assert "it ends today, UTC (2026-06-20)" in run.notes[0]


def test_resolve_prefers_the_site_configs_vessel_settings(tmp_path):
    loaded = site(SITE, tmp_path)
    vessel = openvdm.vessel_settings(
        {"name": "Ride", "contact": {"email": "ops@example.org"}}, "openvdm.yaml"
    )
    run = openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0], vessel)
    assert run.config.vessel_name == "Sally Ride"  # the site config's
    assert run.config.contact_email == "ops@example.org"  # OpenVDM's fills the blank
    assert run.vessel_source == "openvdm.yaml"
    assert "vessel_name is 'Sally Ride' in the site config and 'Ride'" in run.notes[0]


def test_resolve_reads_the_vessel_from_cruise_config_json(tmp_path):
    cruise_dir = tmp_path / "SR2612"
    cruise_dir.mkdir()
    (cruise_dir / "cruise_config.json").write_text(
        json.dumps({"vessel": {"contact": {"institution": "SIO"}}})
    )
    loaded = site(SITE, tmp_path)
    run = openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0])
    assert run.config.contact_institution == "SIO"
    assert run.vessel_source == "cruise_config.json"


def test_the_snapshot_is_a_config_toml(tmp_path):
    loaded = site(SITE, tmp_path)
    run = openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0])
    text = openvdm.cruise_toml(run, "OpenVDM (http://openvdm/)")
    assert text.startswith("# Resolved from OpenVDM (http://openvdm/) for OpenRVDAS\n")
    assert loads_config(text) == run.config


@pytest.mark.parametrize(
    "text, seconds", [("90s", 90), ("30m", 1800), ("6h", 21600), ("1d", 86400)]
)
def test_duration_seconds(text, seconds):
    assert openvdm.duration_seconds(text) == seconds


@pytest.mark.parametrize("text", ["6", "6x", "h", "-1h", ""])
def test_not_a_duration(text):
    with pytest.raises(ValueError, match="isn't a duration"):
        openvdm.duration_seconds(text)


def test_written_within(tmp_path):
    path = tmp_path / "wire_log.csv"
    assert openvdm.written_within(path, 3600) is None
    path.write_text("")
    now = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    assert openvdm.written_within(path, 3600, now + timedelta(minutes=30)) is not None
    assert openvdm.written_within(path, 3600, now + timedelta(hours=2)) is None


# Adding the outputs to OpenVDM's MD5 summary ---------------------------------------------------


def openvdm_install(root: Path) -> Path:
    """A stand-in OpenVDM install with the MD5 summary script, as from OpenVDM 2.16.1"""
    for path in ("venv/bin/python", "utils/update_md5_summary.py", "server/etc/openvdm.yaml"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    return root


class FakeRun:
    """subprocess.run standing in for OpenVDM's script; records the commands"""

    def __init__(self, returncode=0, stderr="", error=None):
        self.commands, self.returncode, self.stderr, self.error = [], returncode, stderr, error

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


def written_outputs(tmp_path):
    loaded = site(SITE, tmp_path)
    run = openvdm.resolve(cruise_config(tmp_path), loaded, "OpenRVDAS", loaded.winches[0])
    run.output_dir.mkdir(parents=True)
    written = [run.output_dir / f"SR2612_CTD_{n}.csv" for n in ("wire_log", "checks")]
    for path in written:
        path.write_text("")
    return run, written


def test_openvdm_install_dir():
    assert (
        openvdm.openvdm_install_dir("/srv/openvdm/server/etc/openvdm.yaml")
        == Path("/srv/openvdm").absolute()
    )


def test_queue_md5_update(tmp_path, monkeypatch):
    install = openvdm_install(tmp_path / "openvdm")
    run, written = written_outputs(tmp_path)
    fake = FakeRun()
    monkeypatch.setattr(openvdm.subprocess, "run", fake)
    assert openvdm.queue_md5_update(install, run, written) is None
    (command,) = fake.commands
    assert command[:4] == [
        str(install / "venv/bin/python"),
        str(install / "utils/update_md5_summary.py"),
        "--cruise-id",
        "SR2612",
    ]
    # By their full paths: OpenVDM checks they're in its directory for the cruise
    output = run.output_dir.absolute()
    assert command[4:] == [
        str(output / "SR2612_CTD_checks.csv"),
        str(output / "SR2612_CTD_wire_log.csv"),
    ]


@pytest.mark.parametrize(
    "fake, expected",
    [
        (FakeRun(1, "Traceback ...\nNo Gearman server"), "exited 1: No Gearman server"),
        (FakeRun(error=subprocess.TimeoutExpired("python", 120)), "couldn't run"),
    ],
)
def test_queue_md5_update_failures(tmp_path, monkeypatch, fake, expected):
    install = openvdm_install(tmp_path / "openvdm")
    run, written = written_outputs(tmp_path)
    monkeypatch.setattr(openvdm.subprocess, "run", fake)
    assert expected in openvdm.queue_md5_update(install, run, written)


def test_queue_md5_update_without_the_script(tmp_path):
    run, written = written_outputs(tmp_path)
    assert "needs OpenVDM 2.16.1" in openvdm.queue_md5_update(tmp_path / "none", run, written)


# The hook end to end ---------------------------------------------------------------------------


@pytest.fixture
def api_response(tmp_path):
    """The getCruiseConfig response openvdm_api serves; tests can change it"""
    return cruise_config(tmp_path / "warehouse")


@pytest.fixture
def openvdm_api(tmp_path, api_response):
    """A stand-in OpenVDM web API serving getCruiseConfig for a cruise under tmp_path"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.endswith("/api/warehouse/getCruiseConfig"):
                self.send_error(404)
                return
            body = json.dumps(api_response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/", tmp_path / "warehouse" / "SR2612"
    server.shutdown()


def openvdm_yaml(tmp_path, site_root: str, vessel: dict | None = None) -> Path:
    """openvdm.yaml in a stand-in OpenVDM install, so the hook finds update_md5_summary.py"""
    install = openvdm_install(tmp_path / "openvdm")
    path = install / "server/etc/openvdm.yaml"
    settings = {"siteRoot": site_root.rstrip("/")}
    if vessel:
        settings["vessel"] = vessel
    path.write_text(yaml.safe_dump(settings))
    return path


@pytest.fixture
def hook(openvdm_api, tmp_path, monkeypatch):
    """The hook's command for OpenRVDAS, the cruise's directory, and the MD5 update's stand-in"""
    site_root, cruise_dir = openvdm_api
    md5_update = FakeRun()
    monkeypatch.setattr(openvdm.subprocess, "run", md5_update)
    command = ["openvdm", "OpenRVDAS", "--openvdm-config", str(openvdm_yaml(tmp_path, site_root))]
    return command, cruise_dir, md5_update


def test_hook_writes_the_outputs_into_the_extra_directory(hook, tmp_path, capsys):
    command, cruise_dir, md5_update = hook
    command += ["--site-config", str(site_config(tmp_path))]

    # Before the first transfer there's nothing to assess
    assert main(command) == 0
    assert "doesn't exist yet" in capsys.readouterr().out
    log = openrvdas(cruise_dir / "OpenRVDAS")

    # A transfer without the winch's files is skipped
    assert main([*command, "--changed-files=raw/MET-2026-06-19.txt"]) == 0
    assert "no new or updated winch files" in capsys.readouterr().out
    assert not (cruise_dir / "Products").exists()

    assert main([*command, f"--changed-files=raw/{log.name}", "--changed-files="]) == 0
    out = capsys.readouterr().out
    assert "SR2612_CTD: " in out and "2 deployments" in out
    output = cruise_dir / "Products/WINCH_QA"
    names = ("config.toml", "wire_log.csv", "checks.csv")
    assert {p.name for p in output.iterdir()} == {f"SR2612_CTD_{n}" for n in names}

    # The wire log has the cruise and vessel from OpenVDM and the site TOML
    text = (output / "SR2612_CTD_wire_log.csv").read_text()
    assert "# vessel: Sally Ride\n" in text and "# cruise_pi: A. Scientist\n" in text
    rows = list(csv.DictReader(line for line in text.splitlines() if not line.startswith("#")))
    assert [r["max_payout"] for r in rows] == ["500.0", "800.0"]

    # `run -c` on the snapshot repeats the run exactly
    snapshot = output / "SR2612_CTD_config.toml"
    assert load_config(snapshot).tension_member.id == "NSF-CTD"
    again = tmp_path / "again"
    assert main(["run", str(cruise_dir / "OpenRVDAS"), "-c", str(snapshot), "-o", str(again)]) == 0
    assert (again / "SR2612_CTD_wire_log.csv").read_text() == text

    # Every file written goes to OpenVDM's MD5 summary
    (md5_command,) = md5_update.commands
    assert md5_command[2:4] == ["--cruise-id", "SR2612"]
    assert set(md5_command[4:]) == {str(output / f"SR2612_CTD_{n}") for n in names}
    assert "queued an MD5 summary update" in out


def test_a_failed_check_is_a_finding_not_a_failed_hook(hook, tmp_path, capsys):
    command, cruise_dir, _ = hook
    openrvdas(cruise_dir / "OpenRVDAS", base=4500)  # over the SWT, 4000 lbf
    assert main([*command, "--site-config", str(site_config(tmp_path))]) == 0
    assert "FAIL  Tension within the safe working tension" in capsys.readouterr().out


def test_a_failed_md5_update_is_a_warning(hook, tmp_path, monkeypatch, caplog):
    command, cruise_dir, _ = hook
    monkeypatch.setattr(openvdm.subprocess, "run", FakeRun(1, "no Gearman"))
    openrvdas(cruise_dir / "OpenRVDAS")
    assert main([*command, "--site-config", str(site_config(tmp_path))]) == 0
    assert "the outputs aren't in OpenVDM's MD5 summary" in caplog.text


def test_hook_reports_failures_to_openvdm_in_one_line(hook, tmp_path, capsys):
    command, _, _ = hook
    command[1] = "XBT"
    text = SITE.replace('transfer = "OpenRVDAS"', 'transfer = ""')
    assert main([*command, "--site-config", str(site_config(tmp_path, text))]) == 1
    err = capsys.readouterr().err
    assert err == (
        "winch-data-processing: OpenVDMError: no collection system transfer named 'XBT'\n"
    )


def test_a_transfer_without_winches_has_nothing_to_do(hook, tmp_path, capsys):
    command, _, _ = hook
    command[1] = "CTD"
    assert main([*command, "--site-config", str(site_config(tmp_path))]) == 0
    assert "CTD: the site config has no [[winch]] in it" in capsys.readouterr().out


def test_min_interval_skips_a_winch_with_a_recent_wire_log(hook, tmp_path, capsys):
    command, cruise_dir, _ = hook
    command += ["--site-config", str(site_config(tmp_path))]
    openrvdas(cruise_dir / "OpenRVDAS")
    assert main([*command, "--min-interval", "6h"]) == 0  # no wire log yet
    assert "2 deployments" in capsys.readouterr().out
    assert main([*command, "--min-interval", "6h"]) == 0
    assert "is newer than --min-interval 6h; nothing to do" in capsys.readouterr().out
    assert main(command) == 0  # without it, as at finalizing
    assert "2 deployments" in capsys.readouterr().out


@pytest.fixture
def two_winches(hook, tmp_path):
    command, cruise_dir, _ = hook
    openrvdas(cruise_dir / "OpenRVDAS", "CTD")
    openrvdas(cruise_dir / "OpenRVDAS", "trawl")
    command += ["--site-config", str(site_config(tmp_path, TWO_WINCHES))]
    return command, cruise_dir


def test_hook_assesses_each_winch_of_its_transfer(two_winches, capsys):
    command, cruise_dir = two_winches
    assert main(command) == 0
    output = cruise_dir / "Products/WINCH_QA"
    assert (output / "SR2612_CTD_wire_log.csv").exists()
    assert (output / "SR2612_trawl_wire_log.csv").exists()


def test_hook_runs_only_the_winches_with_changed_files(two_winches):
    command, cruise_dir = two_winches
    assert main([*command, "--changed-files=raw/trawl-2026-06-19.txt"]) == 0
    output = cruise_dir / "Products/WINCH_QA"
    assert (output / "SR2612_trawl_wire_log.csv").exists()
    assert not (output / "SR2612_CTD_wire_log.csv").exists()


def test_a_failing_winch_does_not_stop_the_others(two_winches, monkeypatch, capsys):
    command, cruise_dir = two_winches
    from winch_data_processing import assess

    real = assess.read_fileset

    def read_fileset(directory, config):
        if config.winch_name == "CTD":
            raise RuntimeError("disk on fire")
        return real(directory, config)

    monkeypatch.setattr(assess, "read_fileset", read_fileset)
    assert main(command) == 1
    err = capsys.readouterr().err
    assert err == (
        "winch-data-processing: OpenVDMError: 1 of 2 winches failed: "
        "CTD: RuntimeError: disk on fire\n"
    )
    assert (cruise_dir / "Products/WINCH_QA/SR2612_trawl_wire_log.csv").exists()


# config-from-openvdm ---------------------------------------------------------------------------


def test_config_from_openvdm_writes_each_winch(two_winches, tmp_path, capsys):
    command, _ = two_winches
    command[0] = "config-from-openvdm"
    output = tmp_path / "configs"
    assert main([*command, "-o", str(output)]) == 0
    assert "SR2612_CTD: vessel settings from openvdm.yaml" in capsys.readouterr().err
    assert load_config(output / "SR2612_CTD.toml").winch_name == "CTD"
    assert load_config(output / "SR2612_trawl.toml").tension_member.id == "NSF-trawl"
    assert main([*command, "-o", str(output)]) == 1
    assert "--force to overwrite" in capsys.readouterr().err
    assert main([*command, "-o", "-"]) == 1
    assert "several winches (CTD, trawl): give -o OUTPUT_DIR" in capsys.readouterr().err


def test_config_from_openvdm_to_stdout(openvdm_api, tmp_path, capsys):
    site_root, _ = openvdm_api
    config = openvdm_yaml(tmp_path, site_root, vessel={"contact": {"email": "ops@example.org"}})
    args = ["config-from-openvdm", "OpenRVDAS", "--openvdm-config", str(config)]
    assert main([*args, "--site-config", str(site_config(tmp_path)), "-o", "-"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"# Resolved from OpenVDM ({site_root}) for OpenRVDAS")
    loaded = loads_config(captured.out)
    assert (loaded.vessel_name, loaded.contact_email) == ("Sally Ride", "ops@example.org")


def test_openvdm_yaml_not_found(tmp_path, capsys):
    missing = tmp_path / "openvdm.yaml"
    args = ["--openvdm-config", str(missing), "--site-config", str(site_config(tmp_path))]
    assert main(["openvdm", "OpenRVDAS", *args]) == 1
    err = capsys.readouterr().err
    assert f"no openvdm.yaml at {missing}" in err and "--cruise-config" in err


def test_the_installed_site_toml_is_the_default(tmp_path, monkeypatch, capsys):
    """Without --site-config: the install's configs/site.toml, wherever the hook runs"""
    missing = tmp_path / "openvdm.yaml"
    args = ["openvdm", "OpenRVDAS", "--openvdm-config", str(missing)]
    assert main(args) == 1
    err = capsys.readouterr().err
    assert f"no site TOML at {openvdm.DEFAULT_SITE_CONFIG}" in err and "site-config" in err
    installed = site_config(tmp_path)
    monkeypatch.setattr(openvdm, "DEFAULT_SITE_CONFIG", installed)
    monkeypatch.chdir(tmp_path.parent)
    assert main(args) == 1  # past the site TOML, to openvdm.yaml
    assert f"no openvdm.yaml at {missing}" in capsys.readouterr().err


def test_a_missing_site_config_is_one_line(tmp_path, capsys):
    missing = tmp_path / "nowhere.toml"
    assert main(["openvdm", "OpenRVDAS", "--site-config", str(missing)]) == 1
    assert (
        capsys.readouterr().err.strip().endswith(f"no site TOML at {missing}; check --site-config")
    )


def test_the_default_site_toml_is_in_the_checkout(tmp_path):
    module = Path(openvdm.__file__)
    assert openvdm.install_site_config(module) == ROOT / "configs" / "site.toml"
    # Not in a checkout (a wheel in site-packages): the current directory's
    wheel = tmp_path / "lib/python3.12/site-packages/winch_data_processing/integrations/openvdm"
    wheel.mkdir(parents=True)
    (wheel / "hook.py").touch()
    assert openvdm.install_site_config(wheel / "hook.py") == Path("configs/site.toml")


def test_without_pyyaml(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml raises ImportError
    path = tmp_path / "openvdm.yaml"
    path.write_text('siteRoot: "http://127.0.0.1/"\n')
    args = ["config-from-openvdm", "OpenRVDAS", "--openvdm-config", str(path), "-o", "-"]
    assert main([*args, "--site-config", str(site_config(tmp_path))]) == 1
    assert "uv sync --extra openvdm" in capsys.readouterr().err


def test_unreachable_openvdm_is_one_line(tmp_path, capsys):
    path = tmp_path / "openvdm.yaml"
    path.write_text('siteRoot: "http://127.0.0.1:9/"\n')  # the discard port: refused
    args = ["config-from-openvdm", "OpenRVDAS", "--openvdm-config", str(path), "-o", "-"]
    assert main([*args, "--site-config", str(site_config(tmp_path))]) == 1
    assert "couldn't reach OpenVDM (127.0.0.1)" in capsys.readouterr().err


# A past cruise, from its cruise_config.json ----------------------------------------------------


def cruise_config_json(cruise_dir: Path, cruise_id="RR2403") -> Path:
    """The cruise_config.json OpenVDM writes into a cruise's directory: getCruiseConfig without
    its warehouse block, whose file names it keeps at the top level, with the vessel the cruise
    was on (OpenVDM's cruise.py, export_cruise_config)
    """
    data = cruise_config(Path("/elsewhere"), cruise_id)
    del data["warehouseConfig"]
    data["md5SummaryFn"] = "MD5_Summary.txt"
    data["vessel"] = {"name": "Roger Revelle", "contact": {"email": "ops@example.org"}}
    cruise_dir.mkdir(parents=True, exist_ok=True)
    path = cruise_dir / "cruise_config.json"
    path.write_text(json.dumps(data))
    return path


def test_load_cruise_json(tmp_path):
    path = cruise_config_json(tmp_path / "RR2403")
    assert openvdm.load_cruise_json(path)["cruiseID"] == "RR2403"
    with pytest.raises(openvdm.OpenVDMError, match="can't read the cruise config"):
        openvdm.load_cruise_json(tmp_path / "missing.json")
    path.write_text('{"cruiseName": "x"}')
    with pytest.raises(openvdm.OpenVDMError, match="isn't an OpenVDM cruise config"):
        openvdm.load_cruise_json(path)


def test_a_cruise_directory_named_after_another_cruise(tmp_path):
    path = cruise_config_json(tmp_path / "RR2403")
    renamed = tmp_path / "RR2403-copy"
    path.parent.rename(renamed)
    with pytest.raises(openvdm.OpenVDMError, match="named 'RR2403-copy', but its cruise is"):
        openvdm.load_cruise_json(renamed / "cruise_config.json")


def test_the_hook_on_a_past_cruise(tmp_path, monkeypatch, capsys):
    """--cruise-config: no OpenVDM API (none is running here), the vessel from the file"""
    cruise_dir = tmp_path / "archive" / "RR2403"
    path = cruise_config_json(cruise_dir)
    openrvdas(cruise_dir / "OpenRVDAS")
    install = openvdm_install(tmp_path / "openvdm")
    md5_update = FakeRun()
    monkeypatch.setattr(openvdm.subprocess, "run", md5_update)
    text = SITE.replace('name = "Sally Ride"', 'name = ""')
    hook = ["openvdm", "OpenRVDAS", "--cruise-config", str(path)]
    hook += ["--site-config", str(site_config(tmp_path, text))]
    hook += ["--openvdm-config", str(install / "server/etc/openvdm.yaml")]
    assert main(hook) == 0
    assert "vessel settings from cruise_config.json" in capsys.readouterr().out
    output = cruise_dir / "Products/WINCH_QA"
    snapshot = output / "RR2403_CTD_config.toml"
    assert snapshot.read_text().startswith(f"# Resolved from {path} for OpenRVDAS")
    assert load_config(snapshot).vessel_name == "Roger Revelle"
    (command,) = md5_update.commands
    assert command[2:4] == ["--cruise-id", "RR2403"]
    assert str(output / "RR2403_CTD_wire_log.csv") in command[4:]


def test_a_past_cruise_away_from_openvdm(tmp_path, monkeypatch, caplog):
    """Without OpenVDM installed, the outputs are written and the MD5 summary is left alone"""
    cruise_dir = tmp_path / "RR2403"
    path = cruise_config_json(cruise_dir)
    openrvdas(cruise_dir / "OpenRVDAS")
    md5_update = FakeRun()
    monkeypatch.setattr(openvdm.subprocess, "run", md5_update)
    nowhere = tmp_path / "no-openvdm/server/etc/openvdm.yaml"
    hook = ["openvdm", "OpenRVDAS", "--cruise-config", str(path), "--openvdm-config", str(nowhere)]
    assert main([*hook, "--site-config", str(site_config(tmp_path))]) == 0
    assert md5_update.commands == []
    assert "the outputs aren't in OpenVDM's MD5 summary: no " in caplog.text
    assert (cruise_dir / "Products/WINCH_QA/RR2403_CTD_wire_log.csv").exists()


def test_config_from_a_past_cruise(tmp_path, capsys):
    path = cruise_config_json(tmp_path / "RR2403")
    args = ["config-from-openvdm", "OpenRVDAS", "--cruise-config", str(path)]
    assert main([*args, "--site-config", str(site_config(tmp_path)), "-o", "-"]) == 0
    loaded = loads_config(capsys.readouterr().out)
    assert (loaded.cruise_id, loaded.cruise_pi, loaded.winch_name) == (
        "RR2403",
        "A. Scientist",
        "CTD",
    )
    assert loaded.cruise_start == date(2026, 6, 19)

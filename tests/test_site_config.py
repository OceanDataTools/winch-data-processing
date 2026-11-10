"""site-config: the per-ship TOML for the OpenVDM hook, from prompts (as nav-qa-processing's)."""

import io

import pytest
import yaml
from conftest import ROOT

from winch_data_processing.cli import main
from winch_data_processing.integrations.openvdm import hook
from winch_data_processing.integrations.openvdm.hook import load_site_config
from winch_data_processing.integrations.openvdm.site_setup import Prompter, prompt_site_config


@pytest.fixture(autouse=True)
def no_openvdm_install(monkeypatch, tmp_path):
    """site-config reads /opt/openvdm's openvdm.yaml when it exists; not in these tests"""
    monkeypatch.setattr(
        "winch_data_processing.integrations.openvdm.cli.DEFAULT_OPENVDM_CONFIG",
        tmp_path / "missing.yaml",
    )
    monkeypatch.setattr(hook, "DEFAULT_SITE_CONFIG", tmp_path / "installed" / "site.toml")


def answer(monkeypatch, *lines: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("".join(f"{line}\n" for line in lines)))


HYDRO = (
    "hydro",  # winch name
    "OpenRVDAS",  # transfer
    "Markey",  # manufacturer
    "DESH-5",  # model
    "",  # data format: required, re-asked
    "lci91",  # no reader, re-asked
    "openrvdas_lci90i",
    "*hydro*.txt",  # files
    "",  # record name: every record
    "",  # tension member id: required, re-asked
    "NSF-CTD-1234",
    "steel",  # kind: re-asked
    "cable",
    "0.322 in. EM cable",  # description
    "ten thousand",  # NBL: a number, re-asked
    "10000",
    "1.8",  # FS: below a cable's lowest, re-asked
    "2.5",
    "0.322",  # d
    "",  # d1
)


def test_a_new_site_toml(tmp_path, monkeypatch, capsys):
    answer(
        monkeypatch,
        "Roger Revelle",
        "Scripps Institution of Oceanography",
        "not an email",  # re-asked
        "tech@example.org",
        "",  # extra directory: the default
        *HYDRO,
        "",  # no more winches
    )
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    err = capsys.readouterr().err
    assert "not an email address" in err and "no reader for lci91" in err
    assert "a cable may not be operated below FS 2" in err
    assert "Add hydro's sheaves ([[winch.sheave]])" in err
    site = load_site_config(output)
    assert (site.vessel_name, site.contact_email) == ("Roger Revelle", "tech@example.org")
    assert site.output_extra_directory == "WINCH_QA"
    (winch,) = site.winches
    assert (winch.name, winch.transfer, winch.files) == ("hydro", "OpenRVDAS", "*hydro*.txt")
    assert winch.tables["winch"] == {"name": "hydro", "manufacturer": "Markey", "model": "DESH-5"}
    assert winch.tables["tension_member"] == {
        "id": "NSF-CTD-1234",
        "kind": "cable",
        "description": "0.322 in. EM cable",
        "nominal_breaking_load": 10000,
        "factor_of_safety": 2.5,
        "diameter": 0.322,
    }
    assert "# [[winch.sheave]]" in output.read_text()


def test_a_delimited_winch_is_asked_its_columns(tmp_path, monkeypatch):
    winch = list(HYDRO)
    winch[6:9] = ["delimited", "*.csv", "", "Time", "", "2", "3", ""]  # tension required
    answer(monkeypatch, "", "", "", "", *winch, "")
    output = tmp_path / "site.toml"
    assert main(["site-config", "-o", str(output)]) == 0
    (winch,) = load_site_config(output).winches
    assert winch.tables["data"]["columns"] == {"time": "Time", "tension": 2, "payout": 3}


def test_writes_where_the_hook_reads_by_default(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    answer(monkeypatch, "Roger Revelle", "", "", "", "")
    assert main(["site-config"]) == 0
    assert f"wrote {hook.DEFAULT_SITE_CONFIG}" in capsys.readouterr().err
    assert load_site_config(hook.DEFAULT_SITE_CONFIG).vessel_name == "Roger Revelle"


def test_writes_to_stdout(monkeypatch, capsys):
    answer(monkeypatch, "Roger Revelle", "", "", "", "")
    assert main(["site-config", "-o", "-"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Per-ship settings for the OpenVDM hook")
    assert 'name = "Roger Revelle"' in out


def test_overwrites_only_with_force_starting_from_the_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "site.toml"
    output.write_text((ROOT / "configs/site.example.toml").read_text())
    assert main(["site-config", "-o", str(output)]) == 1
    assert "--force to overwrite" in capsys.readouterr().err
    # Keep everything but the hydro winch's FS, and drop the trawl winch: Enter for the vessel,
    # contact, email, extra directory, then hydro's name, transfer, manufacturer, model, format,
    # files, record name, id, kind, description and NBL; its FS; Enter for d and d1; - for the
    # trawl winch; Enter for no new one
    answer(monkeypatch, *[""] * 15, "3.0", "", "", "-", "")
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    site = load_site_config(output)
    (hydro,) = site.winches
    assert hydro.tables["tension_member"]["factor_of_safety"] == 3.0
    # What wasn't asked is kept: the sheaves, monitoring and break tests
    example = load_site_config(ROOT / "configs/site.example.toml").winches[0].tables
    for table in ("sheave", "monitoring"):
        assert hydro.tables[table] == example[table]
    assert hydro.tables["tension_member"]["break_test"] == example["tension_member"]["break_test"]


def test_end_of_input_writes_an_existing_file_back_unchanged(tmp_path, monkeypatch):
    output = tmp_path / "site.toml"
    output.write_text((ROOT / "configs/site.example.toml").read_text())
    before = load_site_config(output)
    answer(monkeypatch)  # no input at all
    assert main(["site-config", "-o", str(output), "--force"]) == 0
    assert load_site_config(output) == before


def test_end_of_input_keeps_a_default_that_wouldnt_pass_its_check():
    prompt = Prompter(stdin=io.StringIO(""), out=io.StringIO())
    assert prompt.ask("email", "not an email", lambda text: "bad") == "not an email"


OPENVDM_YAML = {
    "siteRoot": "http://openvdm/",
    "vessel": {
        "name": "Roger Revelle",
        "operator": "SIO",
        "email": "ops@example.org",
        "r2r": {"vesselID": "RR"},
    },
}


def test_openvdm_yaml_values_are_the_defaults(tmp_path, monkeypatch, capsys):
    path = tmp_path / "openvdm.yaml"
    path.write_text(yaml.safe_dump(OPENVDM_YAML))
    answer(monkeypatch, "", "", "", "", "")
    assert main(["site-config", "-o", "-", "--openvdm-config", str(path)]) == 0
    captured = capsys.readouterr()
    assert "openvdm.yaml's vessel block has these" in captured.err
    assert 'contact_email = "ops@example.org"' in captured.out
    assert 'contact_institution = "SIO"' in captured.out


def test_the_site_tomls_values_win_over_openvdm_yamls_as_defaults(tmp_path):
    defaults = load_site_config(ROOT / "configs/site.example.toml")
    prompt = Prompter(stdin=io.StringIO(""), out=io.StringIO())
    site = prompt_site_config(defaults, prompt, {"vessel_name": "Revelle"})
    assert site.vessel_name == "Roger Revelle"


def test_openvdm_yaml_without_a_vessel_block(tmp_path, monkeypatch, capsys):
    path = tmp_path / "openvdm.yaml"
    path.write_text('siteRoot: "http://openvdm/"\n')
    answer(monkeypatch)
    assert main(["site-config", "-o", "-", "--openvdm-config", str(path)]) == 0
    assert "has no vessel block" in capsys.readouterr().err


def test_says_when_openvdm_yaml_is_not_at_the_default_path(monkeypatch, capsys):
    answer(monkeypatch)
    assert main(["site-config", "-o", "-"]) == 0
    assert "No openvdm.yaml at" in capsys.readouterr().err


def test_missing_openvdm_config_is_an_error(tmp_path, capsys):
    missing = tmp_path / "nowhere.yaml"
    assert main(["site-config", "-o", "-", "--openvdm-config", str(missing)]) == 1
    assert f"no openvdm.yaml at {missing}" in capsys.readouterr().err

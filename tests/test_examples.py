import re
import tomllib

from conftest import EXAMPLE, ROOT

from winch_data_processing.config import SCHEMA, Thresholds, load_config
from winch_data_processing.wirerecord import load


def _paths(schema: dict, path: tuple[str, ...] = ()):
    for key, sub in schema.items():
        yield (*path, key), sub is not None
        if sub is not None:
            yield from _paths(sub, (*path, key))


def test_the_example_shows_every_key_commented_or_not():
    text = EXAMPLE.read_text()
    uncommented = re.sub(r"^#\s?", "", text, flags=re.MULTILINE)
    for path, is_table in _paths(SCHEMA):
        if is_table:
            assert re.search(
                rf"^\s*\[\[?{re.escape('.'.join(path))}\]\]?", uncommented, re.MULTILINE
            ), path
        else:
            assert re.search(rf"^\s*{path[-1]}\s*=", uncommented, re.MULTILINE), path


def test_the_examples_thresholds_are_the_defaults():
    table = tomllib.loads(EXAMPLE.read_text())["thresholds"]
    assert table == {name: getattr(Thresholds(), name) for name in Thresholds.__dataclass_fields__}


def test_the_readme_names_every_config_table():
    readme = (ROOT / "README.md").read_text()
    for path, is_table in _paths(SCHEMA):
        if is_table and len(path) == 1:
            assert f"[{path[0]}]" in readme, path


def test_docs_examples_config_and_wire_record_are_valid():
    """docs/examples: its config, and its wire record's files (rebuilt per its README)"""
    examples = ROOT / "docs" / "examples"
    config = load_config(examples / "RR2403_hydro.toml")
    assert config.tension_member.id == "ASSUMED-HYDRO"
    record = load(examples / "wire-record")
    assert [d.cruise for d in record.deployments] == ["RR2403"]
    assert str(record.last_break_test) == "2023-11-15"
    assert all("illustrative" in e.describe() for e in record.events)
